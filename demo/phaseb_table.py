"""Markdown table of the Phase B verification runs (.live/runs/phaseb_*/summary.json).
DEMO RUNS, NOT RESULTS: one live conversation each on a laptop kind cluster."""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main(root: str = ".live/runs") -> None:
    print("| Run | Calls ok (Normal / Skypiea) | Waited on tools Normal | Skypiea | Cold N / S |"
          " Skypiea idle pod-s (N) | Writes (outcome) | Truth verdicts per turn |")
    print("|---|---|---|---|---|---|---|---|")
    for d in sorted(Path(root).glob("phaseb_*")):
        f = d / "summary.json"
        if not f.exists():
            print(f"| {d.name} | no summary (run failed) | | | | | | |")
            continue
        s = json.loads(f.read_text())
        t = s.get("totals") or {}
        b, p = t.get("base", {}), t.get("pred", {})
        w = ", ".join(f"{x['tool']} {x['outcome']}" for x in s.get("writes", [])) or "none"
        tv = []
        for x in s.get("truth", []):
            v = x["verdict"]
            if v == "not_done":
                v += f" (badge; corrected={x.get('corrected')})"
            tv.append(f"t{x['turn']} {v}")
        print(f"| {d.name.removeprefix('phaseb_')} | {b.get('ok')}/{b.get('calls')} / "
              f"{p.get('ok')}/{p.get('calls')} | {b.get('wait_ms', 0) / 1000:.2f} s | "
              f"{p.get('wait_ms', 0) / 1000:.2f} s | {b.get('cold')} / {p.get('cold')} | "
              f"{p.get('idle_pod_s')} ({b.get('idle_pod_s')}) | {w} | {'; '.join(tv)} |")


if __name__ == "__main__":
    main(*sys.argv[1:])
