"""Run N agent sessions of one scenario under one policy and record them.

    # offline-LLM demo (ScriptedLLM: fixed tool order, fake think time; no LLM network)
    python -m agent.run --scenario A --policy predictive --llm scripted --sessions 3
    # live LLM (OpenAI-compatible; AGENT_BASE_URL, AGENT_MODEL, optional AGENT_API_KEY)
    python -m agent.run --scenario B --policy keep_alive --llm openai-compatible --sessions 10

Needs the kind stack and the loopback port-forwards (demo/portforward.sh start). The agent
talks to tools ONLY through the gateway's POST /mcp; the harness (agent/platform.py) sets the
policy and waits for every tool to be back at 0 before and after the run.

Writes to --out-dir (metadata only, no prompts, arguments or tool payloads):
  <run_id>_steps.csv     one row per tool call (think ms, tool, cold, added ms, predicted, ...)
  <run_id>_sessions.csv  one row per session (tool order, followed the scenario graph?)
  <run_id>_run.json      run labels, idle pod-seconds (controller), checks
Predictor profile prd is SYNTHETIC; every output says so.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from agent import tasks
from agent.llm import LLMError, OpenAICompatibleLLM, ScriptedLLM
from agent.loop import SESSION_FIELDS, STEP_FIELDS, run_session
from agent.mcp_client import McpClient
from agent.platform import POLICIES, Platform

LABEL = ("SYNTHETIC predictor profile prd; kind cold starts (laptop, lower bound); "
         "{llm}; {n} sessions")


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", required=True, choices=["A", "B"])
    ap.add_argument("--policy", required=True, choices=sorted(POLICIES))
    ap.add_argument("--llm", default="scripted", choices=["scripted", "openai-compatible"])
    ap.add_argument("--sessions", type=int, default=3)
    ap.add_argument("--first", type=int, default=0, help="index of the first session")
    ap.add_argument("--max-steps", type=int, default=8)
    ap.add_argument("--scripted-think-ms", type=float, default=3000.0)
    ap.add_argument("--gateway", default="http://127.0.0.1:18080/mcp")
    ap.add_argument("--controller", default="http://127.0.0.1:18081")
    ap.add_argument("--stub", default="http://127.0.0.1:18082")
    ap.add_argument("--model-url", default="http://127.0.0.1:18083")
    ap.add_argument("--out-dir", type=Path, default=Path(".demo/agent_runs"))
    ap.add_argument("--run-id", default="")
    ap.add_argument("--no-wait", action="store_true", help="skip the wait for 0 replicas")
    a = ap.parse_args(argv)

    task = tasks.get(a.scenario)
    profile = tasks.PROFILE
    if a.llm == "openai-compatible":
        try:
            live = OpenAICompatibleLLM.from_env()
        except LLMError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        llm_label, model = "openai-compatible", live.model
    else:
        live, llm_label, model = None, "scripted", f"scripted-{a.scripted_think_ms:.0f}ms"
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_id = a.run_id or f"{stamp}_{a.scenario}_{a.policy}_{llm_label}"
    a.out_dir.mkdir(parents=True, exist_ok=True)
    label = LABEL.format(llm=f"LLM {model}" if live else "scripted LLM", n=a.sessions)
    print(f"[agent] run {run_id}: scenario {a.scenario} ({task.name}), policy {a.policy}, "
          f"{a.sessions} sessions\n[agent] {label}", flush=True)

    plat = Platform(a.controller, a.stub, a.model_url)
    waited = 0.0 if a.no_wait else plat.wait_zero()
    before = plat.controller_metrics()
    m_before = plat.model_requests()
    setup = plat.set_policy(a.policy, profile)
    t0 = time.monotonic()
    started = datetime.now(UTC).isoformat(timespec="seconds")
    ctx = {"run_id": run_id, "policy": a.policy, "llm": llm_label, "model": model,
           "profile": profile}
    steps, sessions = [], []
    for i in range(a.first, a.first + a.sessions):
        llm = live or ScriptedLLM(task.scripted(i), think_ms=a.scripted_think_ms)
        mcp = McpClient(a.gateway)
        try:
            res = run_session(llm, mcp, task, i, ctx, max_steps=a.max_steps)
        finally:
            mcp.close()
        steps += res.steps
        sessions.append(res.summary)
        s = res.summary
        colds = sum(1 for r in res.steps if r["cold"] is True)
        print(f"[agent] session {s['session']}: {s['sequence'] or '-'} | end {s['end_reason']} "
              f"| graph {'yes' if s['followed_graph'] else 'no'} | cold {colds}/{s['tool_calls']}"
              + (f" | {s['error'][:100]}" if s["error"] else ""), flush=True)
    sessions_s = time.monotonic() - t0
    if a.policy != "predictive":
        plat.reset()
    drain = 0.0 if a.no_wait else plat.wait_zero()
    after = plat.controller_metrics()
    m_after = plat.model_requests()
    model_delta = None if m_before is None or m_after is None else m_after - m_before
    checks = []
    if a.policy == "predictive" and not model_delta:
        checks.append("predictive run but the model saw no /decide requests")
    if a.policy != "predictive" and model_delta:
        checks.append(f"stub policy run but the model saw {model_delta:.0f} requests")
    run = {"run_id": run_id, "scenario": a.scenario, "task": task.name, "policy": a.policy,
           "llm": llm_label, "model": model, "profile": profile, "label": label,
           "profile_note": tasks.PROFILE_NOTE, "sessions": a.sessions, "first": a.first,
           "max_steps": a.max_steps, "started_utc": started, "sessions_s": round(sessions_s, 1),
           "waited_for_zero_s": round(waited, 1), "drain_s": round(drain, 1),
           "idle_pod_s": round(after["skypiea_pod_seconds_idle_total"]
                               - before["skypiea_pod_seconds_idle_total"], 1),
           "controller_deltas": {k: round(after[k] - before[k], 3) for k in before},
           "model_decide_requests": model_delta, "setup": setup, "checks": checks}
    write_csv(a.out_dir / f"{run_id}_steps.csv", STEP_FIELDS, steps)
    write_csv(a.out_dir / f"{run_id}_sessions.csv", SESSION_FIELDS, sessions)
    (a.out_dir / f"{run_id}_run.json").write_text(json.dumps(run, indent=1) + "\n")
    calls = [r for r in steps if r["ok"] is True]
    colds = sum(1 for r in calls if r["cold"] is True)
    print(f"[agent] done: {len(calls)} ok calls, {colds} cold, idle pod-s {run['idle_pod_s']}, "
          f"graph followed {sum(s['followed_graph'] for s in sessions)}/{len(sessions)}; "
          f"files {a.out_dir}/{run_id}_*" + (f"; CHECKS: {checks}" if checks else ""),
          flush=True)
    return 1 if checks else 0


if __name__ == "__main__":
    raise SystemExit(main())
