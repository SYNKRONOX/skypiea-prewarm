"""Tables for the lead-time demo grid (demo runs, not results).

    python -m demo.leadtime_table results/live_console_leadtime   # -> stdout and <dir>/tables.md

Reads <dir>/<run>/summary.json written by `python -m demo.console_run` (one conversation each)
and groups them by (demo, lookahead, hint mode). Medians are over the conversations of a cell
(n shown); nothing is typed in. Cells where Skypiea waited longer or cost more are listed in
plain words.
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

MODES = ("off", "enter", "typing")
MODE_LABEL = {"off": "hint off", "enter": "hint at Enter", "typing": "hint at typing start"}


def med(xs: list[float]) -> float | None:
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def ms(v: float | None) -> str:
    return "-" if v is None else f"{v:,.0f}"


def load(d: Path) -> dict[tuple, list[dict]]:
    cells: dict[tuple, list[dict]] = defaultdict(list)
    for p in sorted(d.glob("*/summary.json")):
        r = json.loads(p.read_text())
        if "lookahead" not in r or "hint_mode" not in r:   # runs from before this grid
            continue
        r["_name"] = p.parent.name
        cells[(r["demo"], r["lookahead"], r["hint_mode"])].append(r)
    return cells


def cell_rows(runs: list[dict]) -> dict:
    turns: dict[int, list[dict]] = defaultdict(list)
    for r in runs:
        for t in r.get("turns", []):
            turns[t["turn"]].append(t)
    tot = [r["totals"] for r in runs if r.get("totals")]
    return {
        "n": len(runs),
        "turns": {k: {"n": len(v),
                      "base": med([t.get("base_first_wall_ms") for t in v]),
                      "pred": med([t.get("pred_first_wall_ms") for t in v]),
                      "pred_warm": sum(1 for t in v if t.get("pred_first_warm")),
                      "base_warm": sum(1 for t in v if t.get("base_first_cold") is False),
                      "lead": med([t.get("lead_ms") for t in v]),
                      "first_tools": sorted({t.get("first_tool") or "-" for t in v})}
                  for k, v in sorted(turns.items())},
        "calls": sum(t["base"]["calls"] for t in tot),
        "base_cold": sum(t["base"]["cold"] for t in tot),
        "pred_cold": sum(t["pred"]["cold"] for t in tot),
        "base_wait": med([t["base"]["wait_ms"] for t in tot]),
        "pred_wait": med([t["pred"]["wait_ms"] for t in tot]),
        "base_idle": med([t["base"]["idle_pod_s"] for t in tot]),
        "pred_idle": med([t["pred"]["idle_pod_s"] for t in tot]),
        "abandoned": sum(len(r.get("abandoned", [])) for r in runs),
        "from_text": sum(1 for r in runs for c in r.get("calls", [])
                         if c.get("tool_call_from_text")),
        "failed": sum(1 for r in runs for c in r.get("calls", [])
                      if c.get("tool_class") and not (c.get("base_ok") and c.get("pred_ok"))),
    }


def markdown(cells: dict[tuple, list[dict]]) -> str:
    out = ["DEMO RUNS, NOT RESULTS. Autopilot with the fixed customer script; the scripted "
           "customer "
           "types at an assumed 200 characters per minute in every cell (a typing model, not a "
           "measurement); the LLM (llama3.1:8b, temperature 0) is live and picks its own tools. "
           "Local kind on a laptop, both stacks on one node, images pre-pulled (cached starts), "
           "console tool mapping on, baseline scale-to-zero, mirror concurrent. Medians over the "
           "n conversations of a cell.", ""]
    findings = []
    for demo in sorted({k[0] for k in cells}):
        out += [f"## {demo}", "",
                "| lookahead | hint | n | agent waited on tools, median per conversation: "
                "baseline / Skypiea ms | cold calls baseline / Skypiea (of calls) | idle pod-s, "
                "median per conversation: baseline / Skypiea | abandoned turns |",
                "|---|---|---|---|---|---|---|"]
        rows = {}
        for la in (1, 2):
            for mode in MODES:
                runs = cells.get((demo, la, mode))
                if not runs:
                    continue
                c = rows[(la, mode)] = cell_rows(runs)
                out.append(f"| {la} | {MODE_LABEL[mode]} | {c['n']} | {ms(c['base_wait'])} / "
                           f"{ms(c['pred_wait'])} | {c['base_cold']} / {c['pred_cold']} "
                           f"(of {c['calls']}) | {c['base_idle']:.1f} / {c['pred_idle']:.1f} | "
                           f"{c['abandoned']} |")
                if c["pred_wait"] is not None and c["base_wait"] is not None and \
                        c["pred_wait"] > c["base_wait"]:
                    findings.append(f"{demo}, lookahead {la}, {MODE_LABEL[mode]}: Skypiea waited "
                                    f"LONGER than the baseline (median {ms(c['pred_wait'])} vs "
                                    f"{ms(c['base_wait'])} ms).")
                if c["pred_idle"] > c["base_idle"]:
                    findings.append(f"{demo}, lookahead {la}, {MODE_LABEL[mode]}: Skypiea COST "
                                    f"MORE idle pod-seconds (median {c['pred_idle']:.1f} vs "
                                    f"{c['base_idle']:.1f}).")
        out += ["", "First call of each turn (median wall ms over the n conversations; "
                "'warm k/n' = the call found a ready pod; lead = first hint of the turn to "
                "Enter):", "",
                "| lookahead | hint | turn | first tool(s) | baseline ms (warm k/n) | "
                "Skypiea ms (warm k/n) | lead ms |", "|---|---|---|---|---|---|---|"]
        for (la, mode), c in rows.items():
            for t, v in c["turns"].items():
                out.append(f"| {la} | {MODE_LABEL[mode]} | {t} | {', '.join(v['first_tools'])} "
                           f"| {ms(v['base'])} ({v['base_warm']}/{v['n']}) | {ms(v['pred'])} "
                           f"({v['pred_warm']}/{v['n']}) | {ms(v['lead'])} |")
        out.append("")
        notes = [f"{la}/{mode}: {c['from_text']} calls written as text, {c['failed']} calls "
                 f"failed on a stack" for (la, mode), c in rows.items()
                 if c["from_text"] or c["failed"]]
        if notes:
            out += ["LLM behaviour per cell (lookahead/hint): " + "; ".join(notes), ""]
    out += ["## Where Skypiea is slower or costs more", ""]
    out += [f"- {f}" for f in findings] or ["- nowhere in these cells"]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    d = Path((argv or sys.argv[1:] or ["results/live_console_leadtime"])[0])
    md = markdown(load(d))
    (d / "tables.md").write_text(md)
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
