"""CORRECTNESS run of the Skye scenario suite (agent/suite/scenarios.py).

    python -m agent.suite.run --models llama3.1:8b,qwen2.5:7b --reps 2 \
        --out results/suite/before

What is real here: the console's own conversation engine (console/engine.py: the same agent
loop, prompts, tool schemas, argument handling, customer sidebar, truth notes the Live Console
uses) and a real local LLM through Ollama. What is NOT here: no cluster, no gateways, no pods.
The tool calls are answered in-process by the mock database (that is also where the Live
Console's answers come from; the pods only add timing), so correctness does not depend on pod
latency. Timings recorded here are LLM wall times of this machine and are NEVER used for any
claim. Customer turns are scripted per variant.

One JSON per run (scenario, variant, model, rep, pass/fail and the reason, the calls with their
error codes, the transcript) and summary.csv / summary.md. Runs are sequential: one model on
the GPU at a time.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import time
from pathlib import Path
from typing import Any

LABEL = ("CORRECTNESS run: real agent loop and real local LLM, tool handlers in-process, no "
         "cluster and no pods; scripted customer turns; timings are not results")


class _Info:
    ok, cold, added_ms, held_ms, predicted, wall_ms = True, None, None, None, None, 0.0
    error = warm_source = hold_late_ms = None


class NullMcp:
    """No gateway: every call 'succeeds' at once with an empty result (the engine takes the
    answer from the in-process mock database, as the Live Console does)."""
    session_id = None

    def call_tool(self, name: str, args: dict[str, Any]):
        return {"structuredContent": {}}, _Info()

    def close(self) -> None:
        pass


class NullStack:
    def __init__(self, key: str):
        from console.stacks import BASE, PRED
        self.s = {"base": BASE, "pred": PRED}[key]

    async def hint(self, sid, turn):
        return 0

    async def session(self, sid):
        return None


class NullPoller:
    idle: dict = {"base": {}, "pred": {}}

    def contention(self, t0, t1):
        return {"max_starting": None, "samples": 0}


def make_llm(model: str, base_url: str):
    from console.models import make_llm as mk
    try:
        return mk({"id": f"ollama:{model}", "model": model, "provider": "ollama",
                   "base_url": base_url})
    except Exception:  # noqa: BLE001 - older console without per-model options
        from console.llm_stream import StreamingLLM
        return StreamingLLM(base_url, model)


async def run_case(case, model: str, llm, rep: int) -> dict[str, Any]:
    from agent.suite.scenarios import judge, snapshot
    from agent.tasks import get as get_task
    from console.engine import Conversation

    async def publish(ev):
        return None
    settings = {"mirror": "concurrent", "hint": False, "hint_mode": "off",
                "baseline_policy": "scale_to_zero", "lookahead": 1, "console_map": None,
                "truth_note": True}
    conv = Conversation(get_task("skyair"), llm, {"base": NullStack("base"),
                                                  "pred": NullStack("pred")},
                        NullPoller(), publish, settings, mcp_factory=lambda url: NullMcp())
    pristine = snapshot(conv.backend)
    t0 = time.monotonic()
    err = None
    try:
        for line in case.turns:
            await asyncio.wait_for(conv.user_turn(line, "autopilot"), 600)
    except Exception as e:  # noqa: BLE001 - recorded, the run fails
        err = f"{type(e).__name__}: {str(e)[:200]}"
    calls = sorted(conv.calls, key=lambda c: c.get("seq", 0))
    transferred = any(c["tool"] == "transfer_to_human" and not c.get("result_error")
                      for c in calls)
    ok, reason = judge(case, conv.backend, pristine, transferred)
    if err:
        ok, reason = False, f"run error: {err}"
    conv.close()
    transcript = [{"role": m["role"], "text": m.get("content") or "",
                   "tool_calls": [t["function"]["name"] + " " + t["function"]["arguments"]
                                  for t in m.get("tool_calls") or []]}
                  for m in conv.messages[1:] if m["role"] in ("user", "assistant", "tool")]
    return {"label": LABEL, "scenario": case.scenario, "variant": case.variant,
            "model": model, "rep": rep, "pass": ok, "reason": reason, "expect": case.expect,
            "customer_turns": case.turns, "customer_turns_are": "scripted",
            "calls": [{"turn": c["turn"], "tool": c["tool"],
                       "error": c.get("result_error"), "from_text": c.get("tool_call_from_text"),
                       "guard": c.get("guard")} for c in calls],
            "n_calls": len(calls),
            "n_errors": sum(1 for c in calls if c.get("result_error")),
            "llm_replies": len(conv.think_log),
            "truth_badges": sum(len(t.get("badges", [])) for t in conv.truth),
            "transferred": transferred,
            "wall_s_not_a_result": round(time.monotonic() - t0, 1),
            "transcript": transcript}


def summarize(rows: list[dict[str, Any]], out: Path) -> str:
    fields = ["model", "scenario", "variant", "rep", "pass", "reason", "n_calls", "n_errors",
              "llm_replies", "truth_badges"]
    with (out / "summary.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return table(rows)


def table(rows: list[dict[str, Any]]) -> str:
    from agent.suite.scenarios import SCENARIOS
    models = sorted({r["model"] for r in rows})
    lines = [f"_{LABEL}._", "",
             "| Scenario | " + " | ".join(models) + " |",
             "|---|" + "---|" * len(models)]
    for sc in SCENARIOS + ["**all**"]:
        cells = []
        for m in models:
            rs = [r for r in rows if r["model"] == m and (sc == "**all**"
                                                          or r["scenario"] == sc)]
            cells.append(f"{sum(r['pass'] for r in rs)}/{len(rs)}" if rs else "–")
        lines.append(f"| {sc} | " + " | ".join(cells) + " |")
    lines += ["", "Pass rate per rep (variance across reps):", ""]
    for m in models:
        reps = sorted({r["rep"] for r in rows if r["model"] == m})
        per = [sum(r["pass"] for r in rows if r["model"] == m and r["rep"] == k) for k in reps]
        n = sum(1 for r in rows if r["model"] == m and r["rep"] == reps[0]) if reps else 0
        lines.append(f"- {m}: " + ", ".join(f"rep {k}: {p}/{n}" for k, p in zip(reps, per,
                                                                               strict=False)))
    lines += ["", "Failure reasons:", ""]
    for m in models:
        reasons: dict[str, int] = {}
        for r in rows:
            if r["model"] == m and not r["pass"]:
                key = f"{r['scenario']}: {r['reason']}"
                reasons[key] = reasons.get(key, 0) + 1
        for k, v in sorted(reasons.items()):
            lines.append(f"- {m}: {k} (x{v})")
    return "\n".join(lines) + "\n"


async def main_async(a) -> int:
    from agent.skyair.tools import Session
    from agent.suite.scenarios import build
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    cases = build(Session())
    if a.only:
        cases = [c for c in cases if c.scenario in a.only.split(",") or c.key in a.only.split(",")]
    rows = [json.loads(p.read_text()) for p in sorted(out.glob("*__r*.json"))] if a.resume else []
    done = {(r["model"], r["scenario"], r["variant"], r["rep"]) for r in rows}
    for model in a.models.split(","):
        llm = make_llm(model, a.base_url)
        for rep in range(1, a.reps + 1):
            for case in cases:
                if (model, case.scenario, case.variant, rep) in done:
                    continue
                r = await run_case(case, model, llm, rep)
                name = f"{model.replace(':', '_')}__{case.key}__r{rep}.json"
                (out / name).write_text(json.dumps(r, indent=1, default=str) + "\n")
                rows.append(r)
                print(f"{model} r{rep} {case.key}: {'PASS' if r['pass'] else 'FAIL'} "
                      f"({r['reason']}) calls {r['n_calls']} errors {r['n_errors']}",
                      flush=True)
        close = getattr(getattr(llm, "http", None), "aclose", None)
        if close:
            await close()
    md = summarize(rows, out)
    (out / "summary.md").write_text(md)
    print(md)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Skye scenario suite, correctness run")
    ap.add_argument("--models", default="llama3.1:8b")
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--out", default="results/suite/run")
    ap.add_argument("--only", default="", help="scenarios or scenario__variant keys")
    ap.add_argument("--resume", action="store_true", help="skip runs already in --out")
    ap.add_argument("--base-url", default=os.environ.get("AGENT_BASE_URL",
                                                         "http://localhost:11434/v1"))
    return asyncio.run(main_async(ap.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
