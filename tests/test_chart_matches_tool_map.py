"""The Helm chart's tool list must mirror tools/tool_map.yaml (the single source of truth)."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CHART = ROOT / "charts" / "skypiea-tools"


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
