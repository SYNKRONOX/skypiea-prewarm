"""Summarise agent runs: per scenario x policy cold rate and added latency, the LLM think gaps
against the cold-start times in configs/cold_start.yaml, and how often the LLM followed the
scenario graph.

    python -m agent.summarize --dir results/agent_runs --llm openai-compatible
    # -> results/agent_runs/summary.csv, summary_think.csv, summary.md

Every number is computed from the run files; nothing is typed in. The cold-start times are
AWS medians (configs/cold_start.yaml); the runs themselves are on kind, whose cold starts are
shorter (a lower bound), so the hideable fraction against AWS is conservative for kind.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
COLD = ROOT / "configs" / "cold_start.yaml"
POLICY_ORDER = {"scale_to_zero": 0, "keep_alive": 1, "keep_alive_30": 1, "predictive": 2}


def nearest_rank(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    return s[min(len(s), max(1, math.ceil(q / 100 * len(s)))) - 1]


def _r(x: float | None, nd: int = 1) -> float | None:
    return None if x is None else round(x, nd)


def load(d: Path, llm: str | None) -> list[tuple[dict, list[dict], list[dict]]]:
    out = []
    for rj in sorted(d.glob("*_run.json")):
        run = json.loads(rj.read_text())
        if llm and run["llm"] != llm:
            continue
        stem = rj.name[: -len("_run.json")]
        with (d / f"{stem}_steps.csv").open(encoding="utf-8") as f:
            steps = list(csv.DictReader(f))
        with (d / f"{stem}_sessions.csv").open(encoding="utf-8") as f:
            sessions = list(csv.DictReader(f))
        out.append((run, steps, sessions))
    return out


def cold_table() -> dict[str, dict[str, float]]:
    d = yaml.safe_load(COLD.read_text())
    return {k: {"cached": v["cached"]["median_ms"], "uncached": v["uncached"]["median_ms"],
                "env": d["environment"]} for k, v in d["servers"].items()}


def cold_env() -> str:
    return str(yaml.safe_load(COLD.read_text())["environment"])


def tool_rows(steps: list[dict]) -> list[dict]:
    """Calls that reached the gateway and succeeded."""
    return [r for r in steps if r["ok"] == "True"]


def summarize(runs: list[tuple[dict, list[dict], list[dict]]]) -> tuple[list[dict], list[dict]]:
    cold = cold_table()
    rows = []
    think_by_tool: dict[str, list[float]] = {}
    for run, steps, sessions in sorted(runs, key=lambda x: (
            x[0]["scenario"], POLICY_ORDER.get(x[0]["policy"], 9), x[0]["started_utc"])):
        ok = tool_rows(steps)
        added = [float(r["added_ms"]) for r in ok if r["added_ms"] not in ("", "None")]
        n_cold = sum(1 for r in ok if r["cold"] == "True")
        # think gaps = LLM requests that produced a call (first call of a reply)
        think = [float(r["think_ms"]) for r in ok if r["call_in_step"] == "0"]
        # hideable: every call; later calls of a multi-call reply have think 0
        for r in ok:
            think_by_tool.setdefault(r["tool"], []).append(float(r["think_ms"]))
        hide_c = [float(r["think_ms"]) >= cold[r["tool"]]["cached"] for r in ok
                  if r["tool"] in cold]
        multi = sum(1 for r in ok if r["call_in_step"] != "0")
        ends: dict[str, int] = {}
        for s in sessions:
            ends[s["end_reason"]] = ends.get(s["end_reason"], 0) + 1
        rows.append({
            "scenario": run["scenario"], "policy": run["policy"], "model": run["model"],
            "sessions": len(sessions), "tool_calls": len(steps), "ok_calls": len(ok),
            "failed_calls": len(steps) - len(ok), "cold_calls": n_cold,
            "cold_rate_pct": _r(100 * n_cold / len(ok)) if ok else None,
            "added_p50_ms": _r(nearest_rank(added, 50)),
            "added_p99_ms": _r(nearest_rank(added, 99)),
            "idle_pod_s": run["idle_pod_s"],
            "think_median_ms": _r(statistics.median(think)) if think else None,
            "think_p90_ms": _r(nearest_rank(think, 90)),
            "think_ge_cold_cached_pct": _r(100 * sum(hide_c) / len(hide_c)) if hide_c else None,
            "calls_in_multi_call_replies": multi,
            "followed_graph": sum(s["followed_graph"] == "True" for s in sessions),
            "canonical_order": sum(s["canonical_order"] == "True" for s in sessions),
            "end_reasons": ";".join(f"{k}={v}" for k, v in sorted(ends.items())),
            "checks": ";".join(run.get("checks", [])),
            "run_id": run["run_id"],
            "label": run["label"]})
    pooled = [float(r["think_ms"]) for _, steps, _ in runs for r in tool_rows(steps)
              if r["call_in_step"] == "0"]
    allc = [(float(r["think_ms"]), r["tool"]) for _, steps, _ in runs for r in tool_rows(steps)
            if r["tool"] in cold]
    think_rows = []
    if pooled:
        think_rows.append({
            "tool": "ALL", "calls": len(allc), "think_median_ms": _r(statistics.median(pooled)),
            "think_p90_ms": _r(nearest_rank(pooled, 90)), "cold_cached_median_ms": None,
            "cold_uncached_median_ms": None, "cold_start_env": cold_env(),
            "think_ge_cached_pct": _r(100 * sum(x >= cold[t]["cached"] for x, t in allc)
                                      / len(allc)) if allc else None,
            "think_ge_uncached_pct": _r(100 * sum(x >= cold[t]["uncached"] for x, t in allc)
                                        / len(allc)) if allc else None})
    for tool, xs in sorted(think_by_tool.items()):
        c = cold.get(tool)
        think_rows.append({
            "tool": tool, "calls": len(xs), "think_median_ms": _r(statistics.median(xs)),
            "think_p90_ms": _r(nearest_rank(xs, 90)),
            "cold_cached_median_ms": c and c["cached"],
            "cold_uncached_median_ms": c and c["uncached"],
            "cold_start_env": c and c["env"],
            "think_ge_cached_pct": c and _r(100 * sum(x >= c["cached"] for x in xs) / len(xs)),
            "think_ge_uncached_pct": c and _r(100 * sum(x >= c["uncached"] for x in xs) / len(xs))})
    return rows, think_rows


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def _f(x: object) -> str:
    return "-" if x in (None, "") else (f"{x:,.0f}" if isinstance(x, float) else str(x))


def markdown(rows: list[dict], think: list[dict], src: str) -> str:
    out = [f"Source: `{src}/summary.csv` and `{src}/summary_think.csv` "
           "(python -m agent.summarize).", "",
           "**Labels:** kind cold starts (laptop: a lower bound), local 8B model, "
           "SYNTHETIC predictor profile `prd`, "
           f"{rows[0]['sessions'] if rows else '?'} sessions per cell. Real (unscaled) LLM think "
           "times. Not comparable with the AWS tables.", "",
           "| Scenario | Policy | sessions | ok calls | cold % | added p50 ms | added p99 ms | "
           "idle pod-s | LLM followed graph | canonical order | session ends |",
           "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        out.append(f"| {r['scenario']} | {r['policy']} | {r['sessions']} | {r['ok_calls']} | "
                   f"{_f(r['cold_rate_pct']) if r['cold_rate_pct'] is None else r['cold_rate_pct']}"
                   f" | {_f(r['added_p50_ms'])} | {_f(r['added_p99_ms'])} | "
                   f"{_f(r['idle_pod_s'])} | {r['followed_graph']}/{r['sessions']} | "
                   f"{r['canonical_order']}/{r['sessions']} | {r['end_reasons']} |")
    out += ["", "LLM think gap before each tool call (all policies pooled) vs that tool's "
            "median cold start in `configs/cold_start.yaml` (AWS; kind starts are shorter). "
            "A call that is not the first of a multi-call LLM reply has think gap 0. "
            "'think >= cached' is the share of calls whose think gap alone could hide a "
            "cached cold start started at the previous call:", "",
            "| Tool | calls | think median ms | think p90 ms | cold start cached ms | "
            "cold start uncached ms | think >= cached | think >= uncached |",
            "|---|---|---|---|---|---|---|---|"]
    for t in think:
        out.append(f"| {t['tool']} | {t['calls']} | {_f(t['think_median_ms'])} | "
                   f"{_f(t['think_p90_ms'])} | {_f(t['cold_cached_median_ms'])} | "
                   f"{_f(t['cold_uncached_median_ms'])} | {_f(t['think_ge_cached_pct'])}% | "
                   f"{_f(t['think_ge_uncached_pct'])}% |")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", type=Path, default=ROOT / "results" / "agent_runs")
    ap.add_argument("--llm", default="openai-compatible", help="'' = all runs")
    a = ap.parse_args(argv)
    runs = load(a.dir, a.llm or None)
    if not runs:
        print(f"no runs in {a.dir}")
        return 1
    rows, think = summarize(runs)
    write_csv(a.dir / "summary.csv", rows)
    write_csv(a.dir / "summary_think.csv", think)
    try:
        src = str(a.dir.resolve().relative_to(ROOT))
    except ValueError:
        src = str(a.dir)
    md = markdown(rows, think, src)
    (a.dir / "summary.md").write_text(md)
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
