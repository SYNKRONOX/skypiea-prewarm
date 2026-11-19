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
def test_admin_token_reaches_only_the_platform(extra):
    """Gateway (#7), stub predictor POST /admin/policy (#27), controller POST /v1/warm (#23);
    never a tool pod or the model."""
    specs = pod_specs(render(*extra))
    want = {"skypiea-gateway", "skypiea-predictor", "skypiea-controller"}
    assert {n for n, s in specs.items() if uses_admin_secret(s)} == want
    for name in want:
        env = {e["name"]: e for e in specs[name]["containers"][0]["env"]}
        assert env["ADMIN_TOKEN"]["valueFrom"]["secretKeyRef"] == {"name": "skypiea-admin",
                                                                   "key": "admin-token"}
        assert "value" not in env["ADMIN_TOKEN"]


# ---------------- opt-in NetworkPolicy ----------------

GW_CTL = [{"podSelector": {"matchLabels": {"skypiea.io/tool": "skypiea-gateway"}}},
          {"podSelector": {"matchLabels": {"skypiea.io/tool": "skypiea-controller"}}}]


def policies(*args: str) -> dict[str, dict]:
    return {d["metadata"]["name"]: d for d in render(*args) if d["kind"] == "NetworkPolicy"}


def selected(pol: dict) -> set[str]:
    (expr,) = pol["spec"]["podSelector"]["matchExpressions"]
    assert expr["key"] == "skypiea.io/tool" and expr["operator"] == "In"
    return set(expr["values"])


def test_network_policy_is_off_by_default():
    assert policies() == {}
    assert policies(*MODEL) == {}


def test_network_policy_restricts_tools_and_predictors_to_gateway_and_controller():
    on = ("--set", "networkPolicy.enabled=true", *MODEL)
    docs = render(*on)
    pods = {d["spec"]["template"]["metadata"]["labels"]["skypiea.io/tool"] for d in docs
            if d["kind"] == "Deployment"}
    pols = policies(*on)
    assert set(pols) == {"skypiea-tools-ingress", "skypiea-predictor-ingress"}
    tools = selected(pols["skypiea-tools-ingress"])
    assert tools and tools <= pods
    assert selected(pols["skypiea-predictor-ingress"]) == {"skypiea-predictor", "skypiea-model"}
    covered = tools | selected(pols["skypiea-predictor-ingress"])
    # every tool and predictor Deployment is covered; only platform / monitoring are not
    assert pods - covered == {"skypiea-gateway", "skypiea-controller", "skypiea-prometheus",
                              "skypiea-grafana"}
    for p in pols.values():
        assert p["spec"]["policyTypes"] == ["Ingress"]
        assert p["spec"]["ingress"] == [{"from": GW_CTL}]


def test_network_policy_extra_sources_are_appended():
    extra = '[{"podSelector":{"matchLabels":{"skypiea.io/tool":"skypiea-prometheus"}}}]'
    pols = policies("--set", "networkPolicy.enabled=true", "--set-json",
                    f"networkPolicy.extraFrom={extra}")
    for p in pols.values():
        assert p["spec"]["ingress"][0]["from"][:2] == GW_CTL
        assert p["spec"]["ingress"][0]["from"][2]["podSelector"]["matchLabels"] == {
            "skypiea.io/tool": "skypiea-prometheus"}


def test_chart_lints_with_network_policy_on():
    r = subprocess.run(["helm", "lint", str(CHART), "--set", "networkPolicy.enabled=true"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
