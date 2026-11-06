"""Model bake-off on the Skypiea Air autopilot personas (W1 #13). DEMO RUNS, NOT RESULTS.

    python -m console.bakeoff --models llama3.1:8b,qwen2.5:7b --reps 2 \
        --out results/live_bakeoff            # the console must be running (make live)

For every model (switched through the console's own /api/model, which resets) and every
persona in agent/tasks/skye.py BAKEOFF, it resets, plays the scripted customer with the LIVE
model (autopilot), then saves the console's per-conversation report (/api/report), the calls
CSV and the turns CSV. The report is computed from the conversation's own records:

  first_try_valid   share of (turn, tool) groups whose FIRST call returned no error
                    (bad arguments, unknown booking code, unknown tool, not verified, ...)
  calls_valid       share of all calls that returned no error
  invented_codes    calls whose booking code never existed in the database
  invented_prices   money amounts Skye quoted that no tool returned (truth check)
  truth_badges      all truth badges ("Not done ...", "Not from a tool ...")
  text_calls        tool calls the model wrote as text (recovered and flagged)
  completed         the persona's goal reached, judged from the database and the write log
                    (agent/tasks/skye.py scenario_done), not from what Skye said
  think_ms_p50/p95  LLM time per reply (request -> complete reply)

Customer text is scripted; the LLM, the tools and both stacks are live. One conversation per
cell and rep: a demo of behaviour, not a benchmark.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path
from typing import Any

from console.engine import nearest_rank


def report(conv: Any, scenario: str | None) -> dict[str, Any]:
    from agent.tasks.skye import scenario_done

    calls = sorted(conv.calls, key=lambda c: c.get("seq", 0))
    groups: dict[tuple, list[dict]] = {}
    for c in calls:
        groups.setdefault((c["turn"], c["tool"]), []).append(c)

    def valid(c: dict) -> bool:
        return not c.get("result_error") and not (c.get("note") and not c.get("out_of_vocab")) \
            and c.get("pred_ok") is not False
    first = [g[0] for g in groups.values()]
    think = [t["think_ms"] for t in conv.think_log]
    truth = conv.truth
    final = next((m.get("content") or "" for m in reversed(conv.messages)
                  if m.get("role") == "assistant" and (m.get("content") or "").strip()), "")
    done = scenario_done(scenario, conv.backend, conv.writes, calls) if scenario else None
    from console.engine import pricing, totals
    tot = totals(calls, conv.idle_delta(), pricing())
    return {
        "label": "demo run, not a result", "conversation": conv.id, "scenario": scenario,
        "llm_model": getattr(conv.llm, "model", ""), "turns": conv.turn,
        "llm_replies": len(think), "calls": len(calls),
        "first_try_valid": round(sum(map(valid, first)) / len(first), 3) if first else None,
        "calls_valid": round(sum(map(valid, calls)) / len(calls), 3) if calls else None,
        "errors": sorted({c["result_error"] for c in calls if c.get("result_error")}),
        "invented_codes": sum(1 for c in calls if c.get("arg_code_known") is False),
        "invented_prices": sum(len(t.get("prices_unsourced", [])) for t in truth),
        "truth_badges": sum(len(t.get("badges", [])) for t in truth),
        "not_done_badges": sum(1 for t in truth if t.get("verdict") == "not_done"),
        "text_calls": sum(1 for t in conv.think_log if t["from_text"]),
        "handover": any(c["tool"] == "transfer_to_human" for c in calls),
        "writes": [{k: w[k] for k in ("turn", "tool", "outcome")} for w in conv.writes],
        "completed": done,
        "code_in_reply": "def " in final or "```" in final,
        "think_ms_p50": nearest_rank(think, 50), "think_ms_p95": nearest_rank(think, 95),
        "think_ms_total": round(sum(think), 1),
        "wait_ms": {k: tot[k]["wait_ms"] for k in ("base", "pred")},
        "idle_pod_s": {k: tot[k]["idle_pod_s"] for k in ("base", "pred")}}


# ---------------------------------------------------------------- driver (HTTP)
URL = "http://127.0.0.1:8090"


def _wait(cond, timeout_s: float, what: str) -> None:
    t = time.monotonic()
    while not cond():
        if time.monotonic() - t > timeout_s:
            raise SystemExit(f"timeout waiting for {what}")
        time.sleep(1.0)


def run(models: list[str], scenarios: list[str], reps: int, out: Path, hint: str) -> list[dict]:
    import httpx
    c = httpx.Client(base_url=URL, timeout=60)

    def st() -> dict:
        return c.get("/api/state").json()
    rows = []
    out.mkdir(parents=True, exist_ok=True)
    for model in models:
        if st()["settings"].get("llm_model") != model:
            r = c.post("/api/model", json={"id": f"ollama:{model}"})
            if r.status_code >= 400:
                raise SystemExit(f"model {model}: {r.text[:200]}")
            time.sleep(1)
            _wait(lambda m=model: not st()["settings"]["busy"]
                  and st()["settings"].get("llm_model") == m, 600, f"model {model}")
        c.post("/api/hint", json={"mode": hint})
        for rep in range(1, reps + 1):
            for sc in scenarios:
                cid = (st().get("conversation") or {}).get("id")
                c.post("/api/reset")
                _wait(lambda old=cid: not st()["settings"]["busy"]
                      and (st().get("conversation") or {}).get("id") != old, 600, "reset")
                t0 = time.time()
                c.post("/api/autopilot", json={"scenario": sc})
                _wait(lambda: st()["settings"]["autopilot"], 60, "autopilot start")
                _wait(lambda: not st()["settings"]["autopilot"], 2400, "autopilot end")
                time.sleep(3)
                rep_ = c.get("/api/report", params={"scenario": sc}).json()
                rep_.update(rep=rep, hint=hint, wall_s=round(time.time() - t0, 1))
                name = f"{model.replace(':', '_')}__{sc}__r{rep}"
                (out / f"{name}.json").write_text(json.dumps(rep_, indent=2) + "\n")
                (out / f"{name}_calls.csv").write_text(c.get("/api/export.csv").text)
                (out / f"{name}_turns.csv").write_text(c.get("/api/export_turns.csv").text)
                rows.append(rep_)
                print(f"{model} {sc} r{rep}: completed={rep_['completed']} "
                      f"first_try_valid={rep_['first_try_valid']} badges={rep_['truth_badges']}",
                      flush=True)
    return rows


FIELDS = ["llm_model", "scenario", "rep", "turns", "calls", "first_try_valid", "calls_valid",
          "invented_codes", "invented_prices", "truth_badges", "not_done_badges", "text_calls",
          "handover", "completed", "think_ms_p50", "think_ms_p95", "think_ms_total", "wall_s",
          "conversation"]


def table(rows: list[dict]) -> str:
    """Markdown summary per model (sums / means over that model's conversations)."""
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(r["llm_model"], []).append(r)
    head = ("| Model | Conversations | Task completed | First-try valid calls (mean) | "
            "Invented codes | Invented prices | Truth badges | Calls written as text | "
            "LLM think p50 / p95 per reply |\n|---|---|---|---|---|---|---|---|---|\n")
    lines = []
    for m, rs in sorted(by.items()):
        ftv = [r["first_try_valid"] for r in rs if r["first_try_valid"] is not None]
        th = [r["think_ms_p50"] for r in rs if r["think_ms_p50"] is not None]
        th95 = [r["think_ms_p95"] for r in rs if r["think_ms_p95"] is not None]
        lines.append(
            f"| {m} | {len(rs)} | {sum(1 for r in rs if r['completed'])} / {len(rs)} | "
            f"{sum(ftv) / len(ftv):.2f} | {sum(r['invented_codes'] for r in rs)} | "
            f"{sum(r['invented_prices'] for r in rs)} | {sum(r['truth_badges'] for r in rs)} | "
            f"{sum(r['text_calls'] for r in rs)} | "
            f"{(nearest_rank(th, 50) or 0) / 1000:.2f} s / {(max(th95) if th95 else 0) / 1000:.2f}"
            f" s |")
    return head + "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    from agent.tasks.skye import BAKEOFF
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", default="llama3.1:8b,qwen2.5:7b")
    ap.add_argument("--scenarios", default=",".join(BAKEOFF))
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--hint", default="off", choices=["off", "enter", "typing"])
    ap.add_argument("--out", type=Path, default=Path("results/live_bakeoff"))
    ap.add_argument("--table-only", action="store_true", help="rebuild summary from --out")
    a = ap.parse_args(argv)
    if a.table_only:
        rows = [json.loads(p.read_text()) for p in sorted(a.out.glob("*__r*.json"))]
    else:
        rows = run(a.models.split(","), a.scenarios.split(","), a.reps, a.out, a.hint)
        prev = [json.loads(p.read_text()) for p in sorted(a.out.glob("*__r*.json"))]
        rows = prev or rows
    with (a.out / "summary.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    (a.out / "summary.md").write_text("Demo runs, not results (one conversation per cell and "
                                      "rep; live LLM; scripted customer).\n\n" + table(rows))
    print(table(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
