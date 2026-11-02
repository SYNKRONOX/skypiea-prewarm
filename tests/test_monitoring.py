"""Grafana dashboard + Prometheus config: every metric used exists in the code; chart renders."""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from tests.test_chart_matches_tool_map import _render

ROOT = Path(__file__).resolve().parent.parent
DASH = ROOT / "charts" / "skypiea" / "files" / "skypiea-dashboard.json"


def defined_metrics() -> set[str]:
    names = set()
    for f in ["gateway/app.py", "controller/service.py", "tools/common/server.py"]:
        names |= set(re.findall(r'"(skypiea_[a-z_]+)"', (ROOT / f).read_text()))
    out = set()
    for n in names:   # client-side suffixes Prometheus adds
        out |= {n, f"{n}_bucket", f"{n}_count", f"{n}_sum"}
        if not n.endswith("_total"):
            out.add(f"{n}_total")
    return out


def test_dashboard_metrics_exist_and_cover_the_brief():
    dash = json.loads(DASH.read_text())
    exprs = [t["expr"] for p in dash["panels"] for t in p["targets"]]
    used = {m for e in exprs for m in re.findall(r"\b(skypiea_[a-z_]+)", e)}
    missing = used - defined_metrics()
    assert not missing, f"dashboard uses metrics nobody exports: {missing}"
    titles = " | ".join(p["title"].lower() for p in dash["panels"])
    for need in ["cold", "added latency", "warm pool", "idle", "prewarm hit", "predictor latency",
                 "predictor errors"]:
        assert need in titles, need
    assert all(t["datasource"]["uid"] == "skypiea-prom" for p in dash["panels"]
               for t in p["targets"])


def test_monitoring_renders_with_scrape_config_and_no_secret_in_values():
    docs = _render()
    by = {(d["kind"], d["metadata"]["name"]): d for d in docs}
    prom_cfg = yaml.safe_load(by[("ConfigMap", "skypiea-prometheus")]["data"]["prometheus.yml"])
    job = prom_cfg["scrape_configs"][0]
    assert job["kubernetes_sd_configs"][0]["namespaces"]["names"] == ["skypiea-tools"]
    ds = yaml.safe_load(by[("ConfigMap", "skypiea-grafana-provisioning")]["data"]
                        ["datasources.yaml"])
    assert ds["datasources"][0]["uid"] == "skypiea-prom"
    dash = json.loads(by[("ConfigMap", "skypiea-grafana-dashboards")]["data"]
                      ["skypiea-dashboard.json"])
    assert dash["uid"] == "skypiea-overview"
    assert by[("Role", "skypiea-prometheus")]["rules"] == [
        {"apiGroups": [""], "resources": ["pods"], "verbs": ["get", "list", "watch"]}]
    # scrape targets: tools, gateway, controller carry the metrics-port annotation
    ann = {d["metadata"]["name"]: d["spec"]["template"]["metadata"].get("annotations", {})
           for d in docs if d["kind"] == "Deployment"}
    assert ann["run-tests"]["skypiea.io/metrics-port"] == "8080"
    assert ann["skypiea-controller"]["skypiea.io/metrics-port"] == "8081"
    values = (ROOT / "charts" / "skypiea" / "values.yaml").read_text().lower()
    assert "password:" not in values


def test_e0_layout_has_no_monitoring_or_controller():
    docs = _render("platform.enabled=false", "monitoring.enabled=false", "e0Probe.enabled=true")
    names = {d["metadata"]["name"] for d in docs if d["kind"] == "Deployment"}
    assert not names & {"skypiea-prometheus", "skypiea-grafana", "skypiea-controller"}
