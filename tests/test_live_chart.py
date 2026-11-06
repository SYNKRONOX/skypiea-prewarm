"""Live-console chart options are opt-in: defaults render none of them."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CHART = ROOT / "charts" / "skypiea"
pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="needs helm")


def render(*sets: str) -> str:
    args = ["helm", "template", "x", str(CHART), "-n", "skypiea-pred"]
    for s in sets:
        args += ["--set", s]
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


def test_defaults_have_no_live_console_bits() -> None:
    out = render()
    for s in ("SKYPIEA_TURN_HINT", "skypiea-live-dashboard.json", "target_label: namespace",
              "skypiea-prometheus-skypiea-pred"):
        assert s not in out


def test_live_options_render() -> None:
    out = render("platform.gateway.turnHint=true", "monitoring.extraNamespaces={skypiea-base}",
                 "monitoring.liveDashboard=true")
    assert 'name: SKYPIEA_TURN_HINT, value: "1"' in out
    assert "skypiea-live-dashboard.json" in out
    assert "namespaces: {names: [skypiea-pred, skypiea-base]}" in out
    assert "namespace: skypiea-base" in out          # read-only Role in the other namespace


def test_live_dashboard_file_is_generated_and_split_by_namespace() -> None:
    from demo.make_live_dashboard import OUT, dashboard

    assert json.loads(OUT.read_text()) == json.loads(json.dumps(dashboard()))
    d = dashboard()
    assert d["refresh"] == "5s"
    exprs = [t["expr"] for p in d["panels"] for t in p["targets"]]
    assert all("namespace" in e for e in exprs)
    for m in ("skypiea_gateway_hint_warms_total", "skypiea_pod_seconds_idle_total",
              "skypiea_gateway_prewarm_hits_total", "skypiea_gateway_predictor_errors_total",
              "skypiea_warm_pool_size"):
        assert any(m in e for e in exprs), m
