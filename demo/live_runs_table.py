"""Markdown tables for the Live Console verification runs (demo runs, not results).

    python -m demo.live_runs_table results/live_console_demo   # -> stdout and <dir>/runs.md

Reads <dir>/<run>/summary.json (written by `python -m demo.console_run`). Calls are ordered by
their measured start on the predictive stack (pred_t_s). Every number is copied from the
files; nothing is typed in.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

RUN_ORDER = ["support_hint_off", "support_hint_on", "coding_hint_off", "coding_hint_on",
             "support_predictor_killed"]


def ms(v) -> str:
    return "-" if v is None else f"{v:,.0f}"


def load(d: Path) -> list[dict]:
    runs = {p.parent.name: json.loads(p.read_text()) for p in d.glob("*/summary.json")}
    return [runs[k] for k in RUN_ORDER if k in runs] + \
        [v for k, v in sorted(runs.items()) if k not in RUN_ORDER]


def calls(run: dict) -> list[dict]:
    return sorted((c for c in run["calls"] if c.get("tool_class")),
                  key=lambda c: c.get("pred_t_s") or 0.0)


def totals_table(runs: list[dict]) -> str:
    out = ["| run | hint | calls | agent waited on tools, baseline / Skypiea ms | cold calls "
           "baseline / Skypiea | added p95 ms baseline / Skypiea | idle pod-s baseline / "
           "Skypiea | calls warmed by the hint |",
           "|---|---|---|---|---|---|---|---|"]
    for r in runs:
        t, cs = r["totals"], calls(r)
        b, p = t["base"], t["pred"]
        hint_warm = sum(1 for c in cs if c.get("pred_warm_source") == "hint")
        out.append(f"| {r['name']} | {r['hint']} | {len(cs)} | {ms(b['wait_ms'])} / "
                   f"{ms(p['wait_ms'])} | {b['cold']} / {p['cold']} (of {b['ok']} / {p['ok']} "
                   f"ok) | {ms(b['added_p95_ms'])} / {ms(p['added_p95_ms'])} | "
                   f"{b['idle_pod_s']} / {p['idle_pod_s']} | {hint_warm} |")
    return "\n".join(out)


def calls_table(run: dict) -> str:
    out = [f"**{run['name']}** (hint {run['hint']}, mirror {run['mirror']}, baseline "
           f"{run['baseline_policy']}, {run['profile_label']}, {run['model']}, LLM "
           f"{run['llm_model']})", "",
           "| # | turn.reply.pos | tool | LLM think ms | baseline wall ms (cold) | Skypiea wall "
           "ms (cold) | Skypiea predicted (source) | gap ms baseline / Skypiea | node starts "
           "(max) | both ok |", "|---|---|---|---|---|---|---|---|---|---|"]
    for i, c in enumerate(calls(run), 1):
        src = c.get("pred_warm_source") or ("call" if c.get("pred_predicted") else "")
        out.append(
            f"| {i} | {c['turn']}.{c['reply']}.{c['pos_in_reply']} | {c['tool']}"
            f"{' (written as text)' if c.get('tool_call_from_text') else ''} | "
            f"{ms(c['think_ms'])} | {ms(c.get('base_wall_ms'))} "
            f"({'cold' if c.get('base_cold') else 'warm'}) | {ms(c.get('pred_wall_ms'))} "
            f"({'cold' if c.get('pred_cold') else 'warm'}) | "
            f"{'yes' if c.get('pred_predicted') else 'no'}{f' ({src})' if src else ''} | "
            f"{ms(c.get('base_gap_ms'))} / {ms(c.get('pred_gap_ms'))} | "
            f"{c.get('base_contention_max_starting')} | "
            f"{'yes' if c.get('base_ok') and c.get('pred_ok') else 'NO'} |")
    return "\n".join(out)


def gap_mismatches(runs: list[dict], tol_ms: float = 100.0) -> list[str]:
    out = []
    for r in runs:
        for c in calls(r):
            b, p = c.get("base_gap_ms"), c.get("pred_gap_ms")
            if b is not None and p is not None and abs(b - p) > tol_ms:
                out.append(f"{r['name']}: {c['tool']} (turn {c['turn']}) baseline gap "
                           f"{ms(b)} ms vs Skypiea {ms(p)} ms")
    return out


def main(argv: list[str] | None = None) -> int:
    d = Path((argv or sys.argv[1:] or ["results/live_console_demo"])[0])
    runs = load(d)
    parts = ["DEMO RUNS, NOT RESULTS: one live conversation per row (llama3.1:8b, temperature "
             "0), local kind cluster on a laptop (cold starts lower than AWS), both stacks on "
             "one node.", "", totals_table(runs), ""]
    mm = gap_mismatches(runs)
    parts += ["Calls whose baseline gap differed from the Skypiea gap by more than 100 ms:", ""]
    parts += [f"- {m}" for m in mm] or ["- none"]
    parts.append("")
    for r in runs:
        parts += [calls_table(r), ""]
    text = "\n".join(parts)
    (d / "runs.md").write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
