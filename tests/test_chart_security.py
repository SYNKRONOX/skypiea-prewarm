"""Chart security (issue #7): the gateway admin token is a generated Secret mounted only into the
gateway."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")
pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="needs helm")

CHART = Path(__file__).resolve().parents[1] / "charts" / "skypiea"
MODEL = ("--set", "platform.predictor.image.tag=t", "--set", "platform.predictor.coldStartYaml=x")


def render(*args: str) -> list[dict]:
    out = subprocess.run(["helm", "template", "x", str(CHART), "-n", "skypiea-pred", *args],
                         check=True, capture_output=True, text=True).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def pod_specs(docs: list[dict]) -> dict[str, dict]:
    return {d["metadata"]["name"]: d["spec"]["template"]["spec"] for d in docs
            if d["kind"] in ("Deployment", "StatefulSet", "DaemonSet", "Job")}


def uses_admin_secret(spec: dict) -> bool:
    return "skypiea-admin" in str(spec) or "ADMIN_TOKEN" in str(spec)


def _admin_secret(docs: list[dict]) -> dict:
    sec = [d for d in docs if d["kind"] == "Secret" and d["metadata"]["name"] == "skypiea-admin"]
    assert len(sec) == 1
    return sec[0]


def test_admin_token_secret_is_generated_and_not_in_values():
    a, b = _admin_secret(render()), _admin_secret(render())
    assert len(a["data"]["admin-token"]) >= 48
    assert a["data"]["admin-token"] != b["data"]["admin-token"]       # random per install
    for f in CHART.glob("values*.yaml"):                               # never a fixed value
        assert "admin-token" not in f.read_text() and "adminToken" not in f.read_text()


@pytest.mark.parametrize("extra", [(), MODEL])
def test_admin_token_reaches_only_the_gateway(extra):
    specs = pod_specs(render(*extra))
    assert {n for n, s in specs.items() if uses_admin_secret(s)} == {"skypiea-gateway"}
    env = {e["name"]: e for e in specs["skypiea-gateway"]["containers"][0]["env"]}
    assert env["ADMIN_TOKEN"]["valueFrom"]["secretKeyRef"] == {"name": "skypiea-admin",
                                                               "key": "admin-token"}
    assert "value" not in env["ADMIN_TOKEN"]
