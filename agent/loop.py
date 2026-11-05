"""The agent loop: LLM step -> tool calls through the gateway's POST /mcp -> results back to the
LLM, until the LLM calls ``finish`` or the step limit is reached.

Per tool call it records metadata only (never prompts, arguments or tool payloads): the LLM's
think time before the call, the tool, and what the gateway reported (cold, added ms, held ms,
predicted). Per session it records the tool order and whether it is a walk of the scenario
graph.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from agent.llm import FINISH, LLMError, NoToolCallError
from agent.mcp_client import McpClient
from agent.tasks import Task

STEP_FIELDS = ["run_id", "scenario", "policy", "llm", "model", "profile", "session", "step",
               "call_in_step", "think_ms", "tool", "ok", "cold", "predicted", "added_ms",
               "held_ms", "wall_ms", "args_dropped", "error"]
SESSION_FIELDS = ["run_id", "scenario", "policy", "llm", "model", "profile", "session", "steps",
                  "tool_calls", "sequence", "followed_graph", "canonical_order", "end_reason",
                  "error"]
MAX_RESULT_CHARS = 1500


def _safe(name: str) -> str:
    """Tool names the model invents are recorded only as short [A-Za-z0-9_] tokens."""
    return re.sub(r"[^A-Za-z0-9_]", "?", name)[:40]


def render_result(res: Any, ok: bool, error: str | None) -> str:
    """What the LLM sees from a tool call (kept short for small models)."""
    if not ok:
        return json.dumps({"error": error or "tool failed"})
    if isinstance(res, dict):
        if "structuredContent" in res:
            out = json.dumps(res["structuredContent"], default=str)
        else:
            out = "\n".join(c.get("text", "") for c in res.get("content", [])
                            if isinstance(c, dict))
    else:
        out = json.dumps(res, default=str)
    return out[:MAX_RESULT_CHARS]


@dataclass
class SessionResult:
    steps: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)


def run_session(llm: Any, mcp: McpClient, task: Task, index: int, ctx: dict[str, Any],
                max_steps: int = 8) -> SessionResult:
    """One agent session. ``ctx`` holds the run labels (run_id, policy, llm, model, profile)."""
    session = f"{task.scenario}{index:03d}"
    base = {"run_id": ctx["run_id"], "scenario": task.scenario, "policy": ctx["policy"],
            "llm": ctx["llm"], "model": ctx["model"], "profile": ctx["profile"],
            "session": session}
    messages: list[dict[str, Any]] = [{"role": "system", "content": task.system},
                                      {"role": "user", "content": task.user_prompt(index)}]
    out = SessionResult()
    seq: list[str] = []
    end, err = "step_limit", ""
    step = 0
    for step in range(1, max_steps + 1):
        try:
            reply = llm.step(messages, task.tools)
        except NoToolCallError as e:
            end, err = "no_tool_call", str(e)[:200]
            break
        except LLMError as e:
            end, err = "llm_error", str(e)[:200]
            break
        messages.append(reply.message)
        finished = False
        for k, tc in enumerate(reply.tool_calls):
            row = {**base, "step": step, "call_in_step": k,
                   "think_ms": round(reply.think_ms, 1) if k == 0 else 0.0,
                   "tool": _safe(tc.name), "ok": "", "cold": "", "predicted": "",
                   "added_ms": "", "held_ms": "", "wall_ms": "", "args_dropped": 0, "error": ""}
            if tc.name == FINISH:
                finished = True
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": "done"})
                continue
            if tc.name not in task.tool_names:
                row.update(ok=False, error="unknown tool (not sent)")
                out.steps.append(row)
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(
                    {"error": f"unknown tool; available: {task.tool_names + [FINISH]}"})})
                continue
            allowed = task.params(tc.name)
            args = {a: v for a, v in tc.arguments.items() if a in allowed}
            res, info = mcp.call_tool(tc.name, args)
            seq.append(tc.name)
            row.update(ok=info.ok, cold=info.cold, predicted=info.predicted,
                       added_ms=info.added_ms, held_ms=info.held_ms,
                       wall_ms=round(info.wall_ms, 1),
                       args_dropped=len(tc.arguments) - len(args), error=info.error or "")
            out.steps.append(row)
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "content": render_result(res, info.ok, info.error)})
        if finished:
            end = "finish"
            break
    out.summary = {**base, "steps": step, "tool_calls": len(seq), "sequence": ">".join(seq),
                   "followed_graph": task.follows_graph(seq),
                   "canonical_order": seq == task.canonical, "end_reason": end, "error": err}
    return out
