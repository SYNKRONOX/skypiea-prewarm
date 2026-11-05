"""Draw the sessions for the real-timing Scenario A/B runs (laptop; reads data/processed read-only).

    python -m gateway.scenario_sessions --out-calls ~/.cache/skypiea/scenario/sessions.json \
        --out-record results/scenario_sessions.json

  A = tau2_bench, fixed split `test` (the support bundle's source), N sessions
  B = tracelab,   fixed split `test`, sessions under --max-s (600 s) end to end (the coding
      bundle's source; TraceLab sessions run up to hours)
Only sessions whose calls all have timing are eligible. The draw is random.sample over the
sorted eligible ids with a fixed seed, so it can be repeated. The record (committed) holds source,
split, filter, seed and ids, with call counts and durations. The calls file (not committed) also
holds the `train`-split histories that warm up the Markov-1 stand-in (never test sessions).
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

DEFAULT_DATA = Path(__file__).resolve().parent.parent / "data" / "processed"
SETS = {
    "real": {"A": {"source": "tau2_bench", "profile": "support"},
             "B": {"source": "tracelab", "profile": "coding"}},
    # SYNTHETIC (traces/generate_synthetic.py --type prd): PRD Scenario A/B graphs, profile prd
    "prd": {"A": {"source": "synthetic_prd", "group": "syn_branching", "profile": "prd",
                  "synthetic": True},
            "B": {"source": "synthetic_prd", "group": "syn_deterministic", "profile": "prd",
                  "synthetic": True}},
}
SCEN = SETS["real"]


def _calls(g) -> list[dict]:
    g = g.sort_values(["t_offset_ms", "position_in_batch"])
    return [{"tool_class": r.tool, "t_ms": float(r.t_offset_ms), "service_ms": float(r.service_ms),
             "batch_id": int(r.batch_id)} for r in g.itertuples()]


def _eligible(d, split: str, max_s: float | None,
              group: str | None = None) -> dict[str, list[dict]]:
    out = {}
    d = d[d["split"] == split]
    if group:   # group ids are <prefix>_<n>
        d = d[d["group"].str.replace(r"_\d+$", "", regex=True) == group]
    for sid, g in d.groupby("session_id", sort=True):
        if not bool(g["has_timing"].all()):
            continue
        end_s = float((g["t_offset_ms"] + g["service_ms"]).max()) / 1000
        if max_s is not None and end_s > max_s:
            continue
        out[str(sid)] = _calls(g)
    return out


def draw(data: Path, n: int, seed: int, max_s_b: float, n_train: int,
         which: str = "real") -> tuple[dict, dict]:
    import pandas as pd

    calls, record = {}, {"set": which, "seed": seed, "n": n, "scenarios": {}}
    if which == "prd":
        record["SYNTHETIC"] = ("PRD scenario graphs, generated traces; not evidence about real "
                               "workloads; never mixed with the real-trace runs")
    for scen, meta in SETS[which].items():
        d = pd.read_parquet(data / meta["source"] / "calls.parquet")
        # real: only TraceLab (B) needs the cap; prd: both, same cap (A max 2786 s, B 618309 s)
        max_s = max_s_b if (scen == "B" or which == "prd") else None
        test = _eligible(d, "test", max_s, meta.get("group"))
        ids = random.Random(seed).sample(sorted(test), n)
        train = _eligible(d, "train", max_s, meta.get("group"))
        tr_ids = sorted(train) if len(train) <= n_train else \
            random.Random(seed).sample(sorted(train), n_train)
        calls[scen] = {"sessions": {s: test[s] for s in ids},
                       "train": [train[s] for s in tr_ids]}
        record["scenarios"][scen] = {
            **meta, "split": "test (fixed split, column `split` of calls.parquet)",
            "filter": "all calls timed" + (f"; end-to-end <= {max_s:g} s" if max_s else ""),
            "eligible": len(test), "session_ids": ids,
            "calls": [len(test[s]) for s in ids],
            "duration_s": [round(max(c["t_ms"] + c["service_ms"] for c in test[s]) / 1000, 1)
                           for s in ids],
            "markov1_warmup": {"split": "train", "sessions": len(tr_ids), "eligible": len(train)}}
    return calls, record


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=DEFAULT_DATA)
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--seed", type=int, default=20261104)
    ap.add_argument("--max-s", type=float, default=600.0, help="Scenario B session length cap")
    ap.add_argument("--n-train", type=int, default=300, help="Markov-1 warm-up sessions (train)")
    ap.add_argument("--set", default="real", choices=sorted(SETS))
    ap.add_argument("--out-calls", type=Path, required=True)
    ap.add_argument("--out-record", type=Path, required=True)
    a = ap.parse_args()
    calls, record = draw(a.data, a.n, a.seed, a.max_s, a.n_train, a.set)
    a.out_calls.parent.mkdir(parents=True, exist_ok=True)
    a.out_calls.write_text(json.dumps(calls, separators=(",", ":")))
    a.out_record.write_text(json.dumps(record, indent=1) + "\n")
    for s, r in record["scenarios"].items():
        dur = sorted(r["duration_s"])
        print(f"{s}: {r['source']} test, {r['eligible']} eligible, drew {len(r['session_ids'])}; "
              f"calls {sum(r['calls'])}; duration s min {dur[0]} median {dur[len(dur) // 2]} "
              f"max {dur[-1]} sum {sum(dur):.0f}; warm-up {r['markov1_warmup']}")
    print(f"calls file {a.out_calls} ({a.out_calls.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
