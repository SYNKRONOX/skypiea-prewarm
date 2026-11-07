"""FR-9: explain the held_ms values recorded in the AWS runs (read-only; the file is not changed).

    python -m gateway.hold_report [results/scenario_aws_calls.csv]

Before W1, the gateway's per-call `held_ms` was measured from the call's ARRIVAL to the moment
the event loop resumed it after the hold wait (now called `hold_wait_ms`). The wait itself is
`wait_ready(timeout = hold_ms cap)`. This prints how far the recorded values exceed the cap and
how many other calls arrived within 0.3 s of each held call (event-loop contention).
"""

from __future__ import annotations

import csv
import math
import statistics
import sys
from pathlib import Path

CAP_MS = 150.0


def nearest_rank(xs: list[float], q: float) -> float:
    s = sorted(xs)
    return s[min(len(s), max(1, math.ceil(q / 100 * len(s)))) - 1]


def report(path: Path) -> dict:
    rows = list(csv.DictReader(path.open()))
    for r in rows:
        r["_t"], r["_h"] = float(r["t_s"]), float(r["held_ms"] or 0)
    held = [r for r in rows if r["_h"] > 0]
    groups: dict[tuple, list] = {}
    for r in rows:
        groups.setdefault((r["log"], r["scenario"], r["policy"]), []).append(r)

    def others(r: dict) -> int:
        return sum(1 for x in groups[(r["log"], r["scenario"], r["policy"])]
                   if x is not r and abs(x["_t"] - r["_t"]) < 0.3)
    over = [r["_h"] - CAP_MS for r in held if r["_h"] > CAP_MS]
    top = max(held, key=lambda r: r["_h"])
    big = [r for r in held if r["_h"] > 170]
    return {"calls": len(rows), "held_calls": len(held),
            "above_150_5": sum(1 for r in held if r["_h"] > 150.5),
            "overrun_ms": {f"p{q}": round(nearest_rank(over, q), 1) for q in (50, 90, 99)}
            | {"max": round(max(over), 1)},
            "max_held": {"held_ms": top["_h"], "scenario": top["scenario"],
                         "policy": top["policy"], "other_calls_within_0_3s": others(top)},
            "above_170": len(big),
            "above_170_median_other_calls": statistics.median(others(r) for r in big)
            if big else None}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    p = Path(argv[0] if argv else "results/scenario_aws_calls.csv")
    for k, v in report(p).items():
        print(f"{k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
