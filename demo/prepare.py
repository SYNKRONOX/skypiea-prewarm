"""Inputs for `make demo` from a FRESH CLONE: no dataset download, only the committed samples.

    python -m demo.prepare    # -> .demo/ (sessions), models/lgbm-prd-demo-<sha>/ (bundle)

1. SYNTHETIC PRD traces (traces/generate_synthetic.py --type prd) with think and service times
   drawn from the committed samples in data/samples/ (not from data/processed/, which a fresh
   clone does not have) -> .demo/processed/synthetic_prd/.
2. A `prd` predictor bundle trained on those traces (services/predictor/export.build, same code
   path as `make model-all`) -> models/lgbm-prd-demo-<sha>/. It is a DEMO bundle: smaller
   training set than the shipped one, and SYNTHETIC like every prd bundle.
3. N Scenario A (and B) TEST sessions drawn with gateway/scenario_sessions.draw (set prd), with
   every think gap SHORTENED to at most --gap-cap-ms (tool service time to --service-cap-ms)
   so the demo finishes in minutes.
   -> .demo/sessions.json

FUNCTIONAL DEMO, NOT RESULTS: shortened gaps, kind cold starts, SYNTHETIC traces.
Same seed and same commit give the same files.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / ".demo"


def short_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short=7", "HEAD"], cwd=ROOT, check=True,
                              capture_output=True, text=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "nogit"


def shorten(calls: list[dict], cap_ms: float, service_cap_ms: float) -> list[dict]:
    """Same calls and batches; every gap between batches capped at cap_ms and every tool
    service time at service_cap_ms."""
    calls = [{**c, "service_ms": min(c["service_ms"], service_cap_ms)} for c in calls]
    out: list[dict] = []
    by_batch: dict[int, list[dict]] = {}
    order: list[int] = []
    for c in calls:
        if c["batch_id"] not in by_batch:
            order.append(c["batch_id"])
            by_batch[c["batch_id"]] = []
        by_batch[c["batch_id"]].append(c)
    prev_end_old = prev_end_new = None
    for b in order:
        g = by_batch[b]
        start = min(c["t_ms"] for c in g)
        if prev_end_old is None:
            new_start = 0.0
        else:
            new_start = prev_end_new + min(max(0.0, start - prev_end_old), cap_ms)
        for c in g:
            out.append({**c, "t_ms": round(new_start + (c["t_ms"] - start), 1)})
        prev_end_old = max(c["t_ms"] + c["service_ms"] for c in g)
        prev_end_new = max(x["t_ms"] + x["service_ms"] for x in out[-len(g):])
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--n-synthetic", type=int, default=400, help="sessions per PRD scenario")
    ap.add_argument("--sessions", type=int, default=8, help="sessions per scenario to replay")
    ap.add_argument("--gap-cap-ms", type=float, default=6000.0)
    ap.add_argument("--service-cap-ms", type=float, default=2000.0)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args(argv)

    from gateway.scenario_sessions import draw
    from predictors.cold_start import ColdStarts
    from predictors.eval import split_groups
    from services.predictor.export import build
    from traces import generate_synthetic
    from traces.load import SAMPLES

    proc = a.out / "processed"
    if proc.exists():
        shutil.rmtree(proc)
    proc.mkdir(parents=True)
    generate_synthetic.generate("prd", n=a.n_synthetic, seed=a.seed, root=SAMPLES, out_root=proc)

    sha = short_sha()
    bundle = ROOT / "models" / f"lgbm-prd-demo-{sha}"
    if bundle.exists():
        shutil.rmtree(bundle)
    train, val, _ = split_groups("synthetic_prd", "fixed", root=proc)
    build(train, val, bundle, ColdStarts.load(ROOT / "configs" / "cold_start.yaml"),
          "synthetic_prd", None, sha, profile="prd")

    calls, record = draw(proc, a.sessions, a.seed, 600.0, 50, "prd")
    for scen in calls:
        calls[scen]["sessions"] = {s: shorten(c, a.gap_cap_ms, a.service_cap_ms)
                                   for s, c in calls[scen]["sessions"].items()}
    record["demo"] = {"gap_cap_ms": a.gap_cap_ms, "service_cap_ms": a.service_cap_ms,
                      "label": "functional demo, not results",
                      "think_and_service_times_from": "data/samples (committed samples)"}
    (a.out / "sessions.json").write_text(json.dumps(calls, separators=(",", ":")))
    (a.out / "sessions_record.json").write_text(json.dumps(record, indent=1) + "\n")
    (a.out / "bundle_dir").write_text(str(bundle.relative_to(ROOT)) + "\n")
    for s, r in record["scenarios"].items():
        print(f"scenario {s}: {len(r['session_ids'])} SYNTHETIC sessions, {sum(r['calls'])} calls, "
              f"gaps capped at {a.gap_cap_ms:.0f} ms, service at {a.service_cap_ms:.0f} ms")
    print(f"bundle {bundle.relative_to(ROOT)} (SYNTHETIC prd demo bundle)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
