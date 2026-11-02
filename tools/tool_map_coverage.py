"""Traffic share each server covers, per scenario source, under tools/tool_map.yaml.

    python tools/tool_map_coverage.py [PROCESSED_DIR] [--min 95]

Reads <PROCESSED_DIR>/<source>/calls.parquet (column `tool` = class), read-only. Exits 1 if a
scenario source has < --min percent of calls on named (non-fallback) servers, or any class has
no server.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.common.toolmap import load  # noqa: E402

SCENARIOS = ["tracelab", "copilot_2026", "tau_bench", "tau2_bench"]
CONTEXT = ["bfcl"]  # reported, but fallback-heavy by design (generic function calling)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("processed", nargs="?", default="data/processed")
    ap.add_argument("--min", type=float, default=95.0)
    a = ap.parse_args()
    tm = load()
    bad = []
    rows = []
    for src in SCENARIOS + CONTEXT:
        path = Path(a.processed) / src / "calls.parquet"
        if not path.exists():
            print(f"{src}: no {path}, skipped")
            continue
        vc = pd.read_parquet(path, columns=["tool"])["tool"].value_counts()
        n = int(vc.sum())
        for cls, cnt in vc.items():
            srv = tm.server_for(cls)
            if srv is None:
                bad.append(f"{src}: class {cls} has no server")
            rows.append({"source": src, "class": cls, "server": srv or "UNMAPPED",
                         "share": 100 * cnt / n, "n": n})
    df = pd.DataFrame(rows)
    print("\nclass -> server (share of the source's calls, %)")
    for src, g in df.groupby("source", sort=False):
        print(f"\n== {src} (n={g['n'].iloc[0]})")
        for _, r in g.sort_values("share", ascending=False).iterrows():
            print(f"   {r['class']:20s} -> {r['server']:18s} {r['share']:6.2f}")
    print("\nserver coverage per source (%)")
    piv = df.pivot_table(index="server", columns="source", values="share", aggfunc="sum")
    piv = piv.reindex(columns=[s for s in SCENARIOS + CONTEXT if s in piv.columns]).fillna(0)
    print(piv.round(1).to_string())
    for src in [s for s in SCENARIOS if s in piv.columns]:
        named = 100 - piv.loc[tm.fallback, src] if tm.fallback in piv.index else 100.0
        flag = "OK" if named >= a.min else "BELOW"
        print(f"   {src:13s} named servers cover {named:5.1f}%  ({flag}, min {a.min:.0f}%)")
        if named < a.min:
            bad.append(f"{src}: named servers cover only {named:.1f}%")
    for b in bad:
        print("FAIL:", b)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
