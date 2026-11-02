"""The Helm chart's tool list must mirror tools/tool_map.yaml (the single source of truth)."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CHART = ROOT / "charts" / "skypiea"


def test_chart_tools_match_tool_map():
    tool_map = yaml.safe_load((ROOT / "tools" / "tool_map.yaml").read_text())["servers"]
    chart = yaml.safe_load((CHART / "values.yaml").read_text())["tools"]
    assert {k: v["tier"] for k, v in chart.items()} == {k: v["tier"] for k, v in tool_map.items()}


def test_every_tool_has_a_dockerfile_and_server():
    tool_map = yaml.safe_load((ROOT / "tools" / "tool_map.yaml").read_text())["servers"]
    for t, spec in tool_map.items():
        assert (ROOT / "tools" / t / "server.py").is_file(), t
        df = (ROOT / "tools" / t / "Dockerfile").read_text()
        assert "@sha256:" in df and "USER 10001" in df and f"requirements-{spec['tier']}" in df


def test_kind_and_aws_values_differ_only_in_registry_and_pull_secret():
    kind = yaml.safe_load((CHART / "values-kind.yaml").read_text())
    aws = yaml.safe_load((CHART / "values-aws.yaml").read_text())
    assert set(kind) == set(aws) == {"registry", "imagePullSecrets"}


def test_controller_owns_replicas():
    """helm upgrade must keep the live replica count (lookup), not reset it to values."""
    tpl = (CHART / "templates" / "tools.yaml").read_text()
    assert 'lookup "apps/v1" "Deployment"' in tpl and "$live.spec.replicas" in tpl
    assert "skypiea.io/replicas-owner: skypiea-controller" in tpl


def _render(*sets: str) -> list[dict]:
    import shutil
    import subprocess

    import pytest

    if not shutil.which("helm"):
        pytest.skip("helm not installed")
    args = ["helm", "template", "skypiea", str(CHART), "-n", "skypiea-tools",
            "-f", str(CHART / "values-kind.yaml")]
    for s in sets:
        args += ["--set", s]
    out = subprocess.run(args, check=True, capture_output=True, text=True).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def test_platform_least_privilege_and_nothing_exposed():
    docs = _render("e0Probe.enabled=true")
    kinds = {d["kind"] for d in docs}
    assert not kinds & {"ClusterRole", "ClusterRoleBinding"}       # namespaced RBAC only
    for d in docs:
        if d["kind"] == "Service":
            assert d["spec"].get("type", "ClusterIP") == "ClusterIP", d["metadata"]["name"]
        if d["kind"] == "Deployment":
            spec = d["spec"]["template"]["spec"]
            assert not spec.get("hostNetwork")
            for c in spec["containers"]:
                r = c["resources"]
                assert r["requests"]["cpu"] and r["requests"]["memory"], c["name"]
                assert r["limits"]["cpu"] and r["limits"]["memory"], c["name"]
                assert c["securityContext"]["allowPrivilegeEscalation"] is False
    roles = {d["metadata"]["name"]: d["rules"] for d in docs if d["kind"] == "Role"}
    ctl = {(g, r): set(rule["verbs"]) for rule in roles["skypiea-controller"]
           for g in rule["apiGroups"] for r in rule["resources"]}
    assert ctl[("apps", "deployments")] == {"get", "list", "watch", "patch"}
    assert "delete" not in set().union(*ctl.values())
    gw = roles["skypiea-gateway"]
    assert gw == [{"apiGroups": ["discovery.k8s.io"], "resources": ["endpointslices"],
                   "verbs": ["get", "list", "watch"]}]
    names = {d["metadata"]["name"] for d in docs if d["kind"] == "Deployment"}
    assert {"skypiea-controller", "skypiea-gateway", "skypiea-predictor"} <= names
    assert len([n for n in names if not n.startswith(("skypiea-", "e0-"))]) == 16


def test_platform_can_be_disabled_for_e0():
    docs = _render("platform.enabled=false", "e0Probe.enabled=true")
    names = {d["metadata"]["name"] for d in docs if d["kind"] == "Deployment"}
    assert "skypiea-controller" not in names and "e0-probe" in names
