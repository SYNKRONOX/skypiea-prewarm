"""Issue #26 / FR-5: "endpoint returns cold/warm-labelled histograms, graphable in Grafana".
The gateway exports skypiea_gateway_call_latency_seconds{tool, cold} and the dashboard graphs
its p50/p99 per cold label."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from tests.test_gateway import call, make, post

DASH = Path(__file__).resolve().parents[1] / "charts" / "skypiea" / "files" / \
    "skypiea-dashboard.json"


def test_metrics_endpoint_returns_cold_and_warm_labelled_histograms():
    async def go():
        gw, _, tracker, _ = make(ready={"shell": 1}, ready_after=0.3)   # > the 150 ms hold
        await post(gw, call("shell"))                 # warm: pod ready
        await post(gw, call("order_lookup"))          # cold: scaled from zero
        async with httpx.AsyncClient(transport=httpx.ASGITransport(gw.app(background=False)),
                                     base_url="http://gw") as c:
            text = (await c.get("/metrics")).text
        assert "# TYPE skypiea_gateway_call_latency_seconds histogram" in text
        assert 'skypiea_gateway_call_latency_seconds_bucket{cold="false",le="0.05",tool="shell"}' \
            in text
        assert 'skypiea_gateway_call_latency_seconds_count{cold="true",tool="order_lookup"} 1.0' \
            in text
    asyncio.run(go())


def test_grafana_panel_graphs_cold_and_warm_separately():
    dash = json.loads(DASH.read_text())
    panels = [p for p in dash["panels"] if any(
        "skypiea_gateway_call_latency_seconds_bucket" in t["expr"] for t in p["targets"])]
    assert len(panels) == 1
    p = panels[0]
    assert "cold" in p["title"].lower() and "warm" in p["title"].lower()
    exprs = [t["expr"] for t in p["targets"]]
    assert all("by (le, cold)" in e for e in exprs)
    assert {t["legendFormat"] for t in p["targets"]} >= {"p50 cold={{cold}}", "p99 cold={{cold}}"}
    ids = [x["id"] for x in dash["panels"]]
    assert len(ids) == len(set(ids))
