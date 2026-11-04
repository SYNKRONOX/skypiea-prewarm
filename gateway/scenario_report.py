"""Job logs of gateway/scenario_run.py -> results CSVs (every call, and one line per policy).

    python -m gateway.scenario_report LOG [LOG ...] --out results/scenario_aws

Writes <out>_calls.csv (every ROW line) and <out>_summary.csv (every POLICY line, metric deltas
flattened). Nothing is dropped or smoothed: failed calls stay in the calls file with ok=False,
and a policy that errored (POLICY_ERROR) is listed with its error and no numbers.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

CALL_FIELDS = ["dataset", "predictor_image", "platform_image",
               "scenario", "policy", "session", "batch", "tool_class", "gap_ms",
               "service_ms", "t_s", "ok", "cold", "added_ms", "held_ms", "predicted", "wall_ms",
               "error", "log"]
SUM_FIELDS = ["dataset", "predictor_image", "platform_image",
              "scenario", "policy", "sessions", "calls", "ok", "cold", "cold_rate",
              "predicted_calls", "added_ms_p50", "added_ms_p99", "held_ms_max", "idle_pod_s",
              "prewarm_hits", "prewarm_misses", "scaleups_queued", "expired_in_queue",
              "scale_events", "predictor_errors", "not_ready", "model_decide_requests",
              "sessions_s", "drain_s", "model_version", "error", "log"]
MET = {"prewarm_hits": "skypiea_gateway_prewarm_hits_total",
       "prewarm_misses": "skypiea_gateway_prewarm_misses_total",
       "scaleups_queued": "skypiea_scaleups_queued_total",
       "expired_in_queue": "skypiea_scaleups_expired_in_queue_total",
       "scale_events": "skypiea_scale_events_total",
       "predictor_errors": "skypiea_gateway_predictor_errors_total",
       "not_ready": "skypiea_gateway_not_ready_total"}


def parse(paths: list[Path]) -> tuple[list[dict], list[dict]]:
    calls, summ = [], []
    for p in paths:
        scen, ds, imgs = None, "real", {}
        for ln in p.read_text().splitlines():
            if ln.startswith("REAL-TIMING run: scenario "):
                scen = ln.split()[3].rstrip(",")
                ds = ln.split("dataset ")[1].split(",")[0] if "dataset " in ln else "real"
                if ", images " in ln:   # header: "..., images predictor:X platform:Y"
                    pred, plat = ln.rsplit(", images ", 1)[1].split()[:2]
                    imgs = {"predictor_image": pred, "platform_image": plat}
            if ln.startswith("ROW "):
                calls.append({"dataset": ds, **json.loads(ln[4:]), "log": p.name})
            elif ln.startswith("POLICY "):
                d = json.loads(ln[7:])
                row = {"dataset": ds, **{k: d.get(k) for k in SUM_FIELDS if k in d}}
                row.update({k: d["metrics"].get(m) for k, m in MET.items()})
                row["model_version"] = (d.get("setup", {}).get("model") or {}).get(
                    "model_version", "stub")
                row["log"] = p.name
                summ.append(row)
            elif ln.startswith("POLICY_ERROR "):
                pol, err = ln[13:].split(":", 1)
                summ.append({"dataset": ds, **imgs, "scenario": scen, "policy": pol,
                             "error": err.strip(),
                             "log": p.name})
    return calls, summ


def write(path: Path, fields: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("logs", nargs="+", type=Path)
    ap.add_argument("--out", required=True)
    ap.add_argument("--images", action="append", default=[], metavar="LOG=PRED,PLATFORM",
                    help="images for logs written before rows carried them (from the deploy "
                         "record), e.g. A_stub.log=predictor:aabdfd2a9a39,platform:b652e6b")
    a = ap.parse_args()
    calls, summ = parse(a.logs)
    known = {}
    for m in a.images:
        log, imgs = m.split("=", 1)
        pred, plat = imgs.split(",")
        known[log] = {"predictor_image": pred, "platform_image": plat}
    for r in calls + summ:
        if not r.get("predictor_image") and r["log"] in known:
            r.update(known[r["log"]])
        if not r.get("predictor_image"):
            raise SystemExit(f"{r['log']}: rows without image tags; pass --images")
    if len({r.get("dataset", "real") for r in calls + summ}) > 1:
        raise SystemExit("real and SYNTHETIC logs in one report: write them separately")
    write(Path(f"{a.out}_calls.csv"), CALL_FIELDS, calls)
    write(Path(f"{a.out}_summary.csv"), SUM_FIELDS, summ)
    for r in summ:
        if r.get("error"):
            print(f"{r['dataset']} {r['scenario']} {r['policy']:13s} ERROR {r['error']}")
            continue
        def f(k: str, fmt: str = ".0f", r: dict = r) -> str:
            v = r.get(k)
            return "-" if v is None else format(v, fmt)
        print(f"{r['dataset']} {r['scenario']} {r['policy']:13s} calls {r.get('ok')}/"
              f"{r.get('calls')}  cold {f('cold_rate', '.3f')}  added p50 {f('added_ms_p50')} "
              f"ms p99 {f('added_ms_p99')} ms  idle {f('idle_pod_s')} pod-s  predicted "
              f"{r.get('predicted_calls')}  queued {f('scaleups_queued')}")
    print(f"{len(calls)} calls, {len(summ)} policy lines -> {a.out}_*.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
