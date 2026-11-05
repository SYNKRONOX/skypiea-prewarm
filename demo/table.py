"""Print the demo table from gateway/scenario_run.py logs (its `POLICY {json}` lines).

    python -m demo.table .demo/run_scale_to_zero.log .demo/run_predictive.log

FUNCTIONAL DEMO, NOT RESULTS: shortened gaps, kind cold starts, SYNTHETIC prd sessions.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BANNER = ("FUNCTIONAL DEMO, NOT RESULTS: kind cold starts (laptop), SYNTHETIC PRD sessions "
          "(profile prd), shortened think gaps")


def policies(paths: list[Path]) -> list[dict]:
    out = []
    for p in paths:
        for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
            if ln.startswith("POLICY "):
                out.append(json.loads(ln[len("POLICY "):]))
    return out


def fmt(x: float | None, nd: int = 0) -> str:
    return "-" if x is None else f"{x:,.{nd}f}"


def table(rows: list[dict]) -> str:
    head = ("| Scenario | Policy | sessions | calls | ok | cold rate | added p50 ms | added p99 ms "
            "| idle pod-s |")
    lines = [BANNER, "", head, "|" + "---|" * 9]
    for r in rows:
        cr = None if r.get("cold_rate") is None else 100 * r["cold_rate"]
        lines.append(f"| {r['scenario']} | {r['policy']} | {r['sessions']} | {r['calls']} | "
                     f"{r['ok']} | {fmt(cr, 1)}% | {fmt(r.get('added_ms_p50'))} | "
                     f"{fmt(r.get('added_ms_p99'))} | {fmt(r.get('idle_pod_s'))} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    paths = [Path(p) for p in (argv if argv is not None else sys.argv[1:])]
    rows = policies(paths)
    if not rows:
        print("no POLICY lines found in " + ", ".join(map(str, paths)), file=sys.stderr)
        return 1
    print(table(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
