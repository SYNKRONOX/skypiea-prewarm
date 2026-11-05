"""Generate charts/skypiea/files/skypiea-live-dashboard.json ("Skypiea Live: baseline vs
predictive"). Every panel is split by the `namespace` label that the Prometheus of the
predictive stack adds when monitoring.extraNamespaces is set (live console only).

    python -m demo.make_live_dashboard
"""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "charts" / "skypiea" / "files" / \
    "skypiea-live-dashboard.json"
DS = {"type": "prometheus", "uid": "skypiea-prom"}
H = "skypiea_gateway_added_latency_seconds_bucket"


def panel(i: int, title: str, desc: str, targets: list[tuple[str, str]], x: int, y: int,
          unit: str = "short", kind: str = "timeseries", w: int = 12, h: int = 8) -> dict:
    p = {"id": i, "type": kind, "title": title, "description": desc, "datasource": DS,
         "gridPos": {"x": x, "y": y, "w": w, "h": h},
         "fieldConfig": {"defaults": {"unit": unit, "color": {"mode": "palette-classic"},
                                      "custom": {"lineWidth": 2, "fillOpacity": 0}},
                         "overrides": []},
         "options": {"legend": {"displayMode": "list", "placement": "bottom"},
                     "tooltip": {"mode": "multi"}},
         "targets": [{"refId": chr(65 + k), "datasource": DS, "expr": e, "legendFormat": lg}
                     for k, (e, lg) in enumerate(targets)]}
    if kind == "stat":
        p["options"] = {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                        "textMode": "value_and_name", "colorMode": "none"}
    return p


def dashboard() -> dict:
    q = "histogram_quantile({q}, sum by (le, namespace) (rate(" + H + "[1m])))"
    panels = [
        panel(1, "Added latency per call, p50 and p95 (1 min windows)",
              "What the gateway added to each call (hold + cold wait + proxying), per stack. "
              "Per-call values are in the Live Console; Prometheus sees 1-minute windows.",
              [(q.format(q=0.5), "p50 {{namespace}}"), (q.format(q=0.95), "p95 {{namespace}}")],
              0, 0, "s"),
        panel(2, "Cold-call rate (1 min windows)",
              "Calls that found no ready pod and took the cold path, per stack.",
              [("sum by (namespace) (rate(skypiea_gateway_calls_total{cold=\"true\"}[1m])) / "
                "clamp_min(sum by (namespace) (rate(skypiea_gateway_calls_total[1m])), 1e-9)",
                "{{namespace}}")], 12, 0, "percentunit"),
        panel(3, "Calls and cold calls since the stack started",
              "Counters since each gateway started (n shown).",
              [("sum by (namespace) (skypiea_gateway_calls_total)", "calls {{namespace}}"),
               ("sum by (namespace) (skypiea_gateway_calls_total{cold=\"true\"})",
                "cold {{namespace}}")], 0, 8, "short", "stat", 8, 5),
        panel(4, "Added latency p50 / p95 since start",
              "Over every call since the gateway started (histogram buckets, so values are "
              "bucket-interpolated).",
              [("histogram_quantile(0.5, sum by (le, namespace) (" + H + "))", "p50 {{namespace}}"),
               ("histogram_quantile(0.95, sum by (le, namespace) (" + H + "))",
                "p95 {{namespace}}")], 8, 8, "s", "stat", 8, 5),
        panel(5, "Idle pod-seconds since start",
              "Controller counter skypiea_pod_seconds_idle_total: ready pods with no call in "
              "flight, summed over time.",
              [("sum by (namespace) (skypiea_pod_seconds_idle_total)", "{{namespace}}")],
              16, 8, "s", "stat", 8, 5),
        panel(6, "Warm pool size (replicas, all tools)",
              "Controller gauge skypiea_warm_pool_size summed over tools, per stack.",
              [("sum by (namespace) (skypiea_warm_pool_size)", "{{namespace}}")], 0, 13),
        panel(7, "Idle pod-seconds (rate)",
              "How many pods are idle right now, on average over 1 min.",
              [("sum by (namespace) (rate(skypiea_pod_seconds_idle_total[1m]))",
                "{{namespace}}")], 12, 13),
        panel(8, "Prewarm hits and misses (per 1 min)",
              "Calls that found a ready pod on arrival (hit) or not (miss).",
              [("sum by (namespace) (increase(skypiea_gateway_prewarm_hits_total[1m]))",
                "hits {{namespace}}"),
               ("sum by (namespace) (increase(skypiea_gateway_prewarm_misses_total[1m]))",
                "misses {{namespace}}")], 0, 21),
        panel(9, "Turn-start hints and the warms they caused (per 1 min)",
              "skypiea_gateway_hints_total and skypiea_gateway_hint_warms_total (only with the "
              "turn hint on; the baseline stack never has it).",
              [("sum by (namespace) (increase(skypiea_gateway_hints_total[1m]))",
                "hints {{namespace}}"),
               ("sum by (namespace) (increase(skypiea_gateway_hint_warms_total[1m]))",
                "hint warms {{namespace}}")], 12, 21),
        panel(10, "Predictor round trip p50 / p95 (gateway view)",
              "Time from the gateway's /decide request to the answer (off the call path).",
              [("histogram_quantile(0.5, sum by (le, namespace) (rate("
                "skypiea_gateway_predictor_latency_seconds_bucket[1m])))", "p50 {{namespace}}"),
               ("histogram_quantile(0.95, sum by (le, namespace) (rate("
                "skypiea_gateway_predictor_latency_seconds_bucket[1m])))",
                "p95 {{namespace}}")], 0, 29, "s"),
        panel(11, "Predictor errors (per 1 min)",
              "Predictor failures the gateway ignored (FR-4), by reason. Killing the predictor "
              "in the console shows up here.",
              [("sum by (namespace, reason) (increase("
                "skypiea_gateway_predictor_errors_total[1m]))", "{{namespace}} {{reason}}")],
              12, 29),
    ]
    return {"uid": "skypiea-live", "title": "Skypiea Live: baseline vs predictive",
            "schemaVersion": 39, "version": 1, "editable": False,
            "time": {"from": "now-15m", "to": "now"}, "refresh": "5s",
            "tags": ["skypiea", "live"], "timezone": "browser", "panels": panels,
            "description": "Live console: the baseline stack (skypiea-base) and the predictive "
                           "stack (skypiea-pred) on one kind node. Local laptop: cold starts are "
                           "lower than on AWS. Functional demo, not results."}


def main() -> int:
    OUT.write_text(json.dumps(dashboard(), indent=1) + "\n")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
