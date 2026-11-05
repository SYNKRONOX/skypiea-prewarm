"""Audit (FR-7, NFR-6): the predictor keeps no session data on disk, in logs or in metrics.

    python tools_audit/audit_no_persistence.py --model models/<version> --cold-start PATH

Starts the service as a child process in an empty working directory, sends 200 sessions whose
session id and tool names carry a unique marker, then:
1. scans every file under the working directory and every file under /tmp created or
   modified since the start, for the marker (bytes);
2. scans the child's stdout/stderr (its logs) for the marker;
3. scans GET /metrics and GET /healthz for the marker;
4. confirms no file was created or modified under the working directory by /decide.
Then a chaos check: start a reload, kill the process (SIGKILL) while it runs, restart, and
confirm the service serves the model from MODEL_PATH. Exit 0 only if every check passes.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def _port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _start(model: Path, cold: Path, cwd: Path, port: int, log: Path, token: str
           ) -> subprocess.Popen:
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(ROOT),
           "MODEL_PATH": str(model), "COLD_START_PATH": str(cold), "PORT": str(port),
           "HOST": "127.0.0.1", "ADMIN_TOKEN": token, "MODEL_ROOT": str(model.parent),
           "PYTHONDONTWRITEBYTECODE": "1"}
    fh = log.open("ab")
    return subprocess.Popen([sys.executable, "-m", "services.predictor"], cwd=str(cwd),
                            env=env, stdout=fh, stderr=subprocess.STDOUT)


def _ready(url: str, timeout: float = 60.0) -> dict:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        try:
            r = httpx.get(f"{url}/healthz", timeout=2.0)
            if r.status_code == 200:
                return r.json()
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise RuntimeError("service not ready")


def _files_with(marker: bytes, root: Path, since: float | None = None) -> list[str]:
    hits = []
    for p in root.rglob("*"):
        try:
            if not p.is_file() or p.is_symlink():
                continue
            if since is not None and p.stat().st_mtime < since:
                continue
            if marker in p.read_bytes():
                hits.append(str(p))
        except OSError:
            continue
    return hits


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", required=True)
    ap.add_argument("--cold-start", required=True)
    a = ap.parse_args(argv)
    model, cold = Path(a.model).resolve(), Path(a.cold_start).resolve()
    marker = f"mk{uuid.uuid4().hex[:20]}"
    token = uuid.uuid4().hex
    checks: dict[str, bool] = {}
    with tempfile.TemporaryDirectory(prefix="skypiea-audit-") as tmp:
        work, log = Path(tmp) / "cwd", Path(tmp) / "service.log"
        work.mkdir()
        port = _port()
        url = f"http://127.0.0.1:{port}"
        t_start = time.time()
        child = _start(model, cold, work, port, log, token)
        try:
            _ready(url)
            before = sorted(str(p) for p in work.rglob("*"))
            with httpx.Client(base_url=url, timeout=5.0) as c:
                for i in range(200):
                    hist = [{"tool_class": t, "t_start_ms": 1000.0 * j,
                             "t_end_ms": 1000.0 * j + 10.0, "error": False, "batch_id": j}
                            for j, t in enumerate(["read_file", f"{marker}_tool", "search"])]
                    c.post("/decide", json={"session_id": f"{marker}{i}", "now_ms": 3000.0,
                                            "history": hist})
                metrics, health = c.get("/metrics").text, c.get("/healthz").text
            after = sorted(str(p) for p in work.rglob("*"))
            checks["no files written by /decide"] = before == after
            checks["marker not in working dir"] = not _files_with(marker.encode(), work)
            checks["marker not in new /tmp files"] = not [
                h for h in _files_with(marker.encode(), Path("/tmp"), t_start)
                if not h.startswith(tmp)]
            checks["marker not in /metrics or /healthz"] = marker not in metrics + health
        finally:
            child.terminate()
            child.wait(timeout=10)
        checks["marker not in logs"] = marker.encode() not in log.read_bytes()

        # chaos: kill -9 during a reload, restart, the configured model is served
        port = _port()
        url = f"http://127.0.0.1:{port}"
        child = _start(model, cold, work, port, log, token)
        try:
            want = _ready(url)["model_version"]
            try:
                httpx.post(f"{url}/reload", json={"model_uri": model.name},
                           headers={"X-Admin-Token": token}, timeout=0.05)
            except httpx.HTTPError:
                pass  # we do not wait for it
            os.kill(child.pid, signal.SIGKILL)
            child.wait(timeout=10)
        finally:
            if child.poll() is None:
                child.kill()
        port = _port()
        url = f"http://127.0.0.1:{port}"
        child = _start(model, cold, work, port, log, token)
        try:
            checks["restart after kill serves the configured model"] = (
                _ready(url)["model_version"] == want)
        finally:
            child.terminate()
            child.wait(timeout=10)
    print(json.dumps(checks, indent=1))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
