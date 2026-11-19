"""demo/session_auth_check.sh (make review Tier 1, issue #7) must FAIL on the vulnerable gateway.

First version: it treated ANY error from `kubectl get --raw .../v1/sessions` as "refused" (on
kind the 401 shows only as kubectl's "You must be logged in to the server"), and it never sent
a wrong token. It now port-forwards to the gateway pod and requires the gateway's own 401 for
no token AND a wrong token, no session id in the refusal, and the Secret referenced only by the
gateway, the controller (/v1/warm, issue #23) and the stub predictor (/admin/policy, issue #27).
Here: a fake kubectl and a stub gateway on loopback."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "demo/session_auth_check.sh"

pytestmark = pytest.mark.skipif(not shutil.which("curl"), reason="needs curl")

GATEWAY = {"kind": "Deployment", "metadata": {"name": "skypiea-gateway"}, "spec": {"template": {
    "spec": {"containers": [{"env": [{"name": "ADMIN_TOKEN", "valueFrom": {"secretKeyRef": {
        "name": "skypiea-admin", "key": "admin-token"}}}]}]}}}}


def _user(name: str) -> dict:
    d = json.loads(json.dumps(GATEWAY))
    d["metadata"]["name"] = name
    return d


TOOL = {"kind": "Deployment", "metadata": {"name": "skypiea-tool-read-file"},
        "spec": {"template": {"spec": {"containers": [{"env": []}]}}}}

FAKE_KUBECTL = r"""#!/usr/bin/env bash
# fake kubectl: --context X -n Y <verb> ...
args=("$@"); set -- "${args[@]:4}"
case "$1 $2" in
  "get pod") echo -n skypiea-gateway-abc ;;
  "get secret") echo secret/skypiea-admin ;;
  "get deploy") echo -n "$FAKE_SECRETREF" ;;   # the gateway's ADMIN_TOKEN secretKeyRef name
  "get deploy,statefulset,daemonset,job,cronjob") cat "$FAKE_WORKLOADS" ;;
  port-forward*) echo "Forwarding from 127.0.0.1:$FAKE_PORT -> 8080"; exec sleep 30 ;;
  *) echo "fake kubectl: unexpected $*" >&2; exit 2 ;;
esac
"""


class Gateway(BaseHTTPRequestHandler):
    mode = "fixed"

    def do_GET(self):  # noqa: N802
        if self.mode == "vulnerable":            # before fix/redteam-1
            code, body = 200, {"sessions": [{"session_id": "victim", "calls": 3}]}
        elif self.mode == "fixed":
            ok = self.headers.get("X-Admin-Token") == "the-real-token"
            code, body = (200, {"sessions": []}) if ok else (401, {"error": "bad admin token"})
        else:                                    # something else answers 401 (not the gateway)
            code, body = 401, {"message": "Unauthorized"}
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


@pytest.fixture
def run(tmp_path):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Gateway)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "kubectl").write_text(FAKE_KUBECTL)
    (bindir / "kubectl").chmod(0o755)
    cli = tmp_path / "skypiea"
    cli.write_text("#!/usr/bin/env bash\necho 'session  calls  ended'\n")
    cli.chmod(0o755)

    def go(mode="fixed", workloads=(GATEWAY,), secretref="skypiea-admin"):
        Gateway.mode = mode
        wl = tmp_path / "workloads.json"
        wl.write_text(json.dumps({"items": list(workloads)}))
        env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "SKYPIEA_BIN": str(cli),
               "FAKE_PORT": str(srv.server_address[1]), "FAKE_WORKLOADS": str(wl),
               "FAKE_SECRETREF": secretref, "KIND_CTX": "kind-test", "NS": "ns"}
        return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True,
                              timeout=60)
    yield go
    srv.shutdown()


def test_passes_on_the_fixed_gateway(run):
    r = run()
    assert r.returncode == 0, r.stderr
    assert r.stdout.count("-> 401") == 4 and "wrong token -> 401" in r.stdout
    assert "the-real-token" not in r.stdout + r.stderr


def test_fails_on_the_vulnerable_gateway(run):
    r = run("vulnerable")
    assert r.returncode != 0 and "answered 200" in r.stderr


def test_fails_when_the_401_is_not_the_gateways(run):
    r = run("other401")
    assert r.returncode != 0 and "not the gateway's refusal" in r.stderr


def test_passes_with_the_controller_and_predictor_token(run):
    r = run(workloads=(GATEWAY, _user("skypiea-controller"), _user("skypiea-predictor")))
    assert r.returncode == 0, r.stderr


def test_fails_when_another_workload_gets_the_secret(run):
    leaky = json.loads(json.dumps(TOOL).replace('"env": []', json.dumps(
        {"env": [{"name": "X", "valueFrom": {"secretKeyRef": {"name": "skypiea-admin"}}}]})[1:-1]))
    r = run(workloads=(GATEWAY, _user("skypiea-controller"), leaky))
    assert r.returncode != 0 and "Deployment/skypiea-tool-read-file" in r.stderr


def test_fails_when_the_gateway_has_no_token_from_the_secret(run):
    r = run(secretref="")
    assert r.returncode != 0 and "does not come from Secret" in r.stderr
