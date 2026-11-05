"""Mirrored conversation engine of the Live Console.

One conversation = one MCP session id, used on BOTH gateways. Every tool call the LLM makes is
sent to the baseline stack and to the predictive stack with the same arguments:
  concurrent (default): both at once - most visual; the two stacks' pod starts can compete
                        for the node (recorded per call as start contention);
  sequential:           predictive first, then baseline - no contention between the two
                        calls (the predictive stack's own warm-ups may still be starting).
The conversation continues with the PREDICTIVE stack's result. The mock tools are
deterministic, so both results should be identical; any difference is flagged.

Recorded per call (metadata only: no prompts, arguments or results are stored):
turn, LLM reply id and position in the reply, LLM think time before the reply, tool and
class, and per stack wall ms, added ms, held ms, cold, predicted, warm source (hint/call),
ok, start contention; plus the baseline policy, mirror mode and hint setting at that moment.
Values come from the gateways' response headers and the controllers' state; nothing is
estimated.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import math
import time
import uuid
from pathlib import Path
from typing import Any

import yaml

from agent.llm import LLMError
from agent.loop import render_result
from agent.mcp_client import McpClient
from agent.tasks import Task

ROOT = Path(__file__).resolve().parent.parent
MAX_STEPS = 8
CSV_FIELDS = ["conversation", "turn", "reply", "pos_in_reply", "replay", "llm_model",
              "think_ms", "tool_call_from_text", "tool", "tool_class", "server", "mirror_mode",
              "hint",
              "baseline_policy", "profile", "results_match"] + [
    f"{s}_{f}" for s in ("base", "pred") for f in (
        "wall_ms", "added_ms", "held_ms", "cold", "predicted", "warm_source", "ok",
        "contention_max_starting", "contention_samples")]


def nearest_rank(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    return s[min(len(s), max(1, math.ceil(q / 100 * len(s)))) - 1]


def pricing() -> dict[str, Any]:
    p = yaml.safe_load((ROOT / "configs" / "model" / "pricing.yaml").read_text())
    tm = yaml.safe_load((ROOT / "tools" / "tool_map.yaml").read_text())
    return {"vcpu_second_usd": float(p["vcpu_second_usd"]), "status": p["status"],
            "source": p["vcpu_second_source"],
            "cores": {s: float(p["cpu_request_cores"][spec["tier"]])
                      for s, spec in tm["servers"].items()}}


def totals(calls: list[dict[str, Any]], idle_delta: dict[str, dict[str, float]],
           price: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for s in ("base", "pred"):
        ok = [c for c in calls if c.get(f"{s}_ok")]
        added = [c[f"{s}_added_ms"] for c in ok if c.get(f"{s}_added_ms") is not None]
        idle = idle_delta.get(s, {})
        cost = sum(v * price["cores"].get(k, 0.0) for k, v in idle.items()) * \
            price["vcpu_second_usd"]
        out[s] = {"calls": len(calls), "ok": len(ok),
                  "wait_ms": round(sum(c[f"{s}_wall_ms"] for c in ok), 1),
                  "cold": sum(1 for c in ok if c.get(f"{s}_cold")),
                  "added_p50_ms": nearest_rank(added, 50),
                  "added_p95_ms": nearest_rank(added, 95), "n_added": len(added),
                  "idle_pod_s": round(sum(idle.values()), 1),
                  "idle_cost_usd": cost}
    return out


def result_key(res: Any) -> str:
    if isinstance(res, dict):
        res = res.get("structuredContent", res.get("content"))
    return json.dumps(res, sort_keys=True, default=str)


def evaluate_decisions(session: dict[str, Any]) -> list[dict[str, Any]]:
    """Each predictor decision with the call that actually came next (if any yet)."""
    calls = session.get("calls", [])
    out = []
    for d in session.get("decisions", []):
        nxt_i = int(d.get("after_call", -1)) + 1
        nxt = calls[nxt_i] if nxt_i < len(calls) else None
        warm = sorted({x["tool_class"] for x in d.get("decisions", [])
                       if x.get("warm") and x.get("copies", 0) > 0})
        ranked = d.get("ranked", [])[:3]
        e = {"trigger": d.get("trigger") or "after_call", "turn": d.get("turn"),
             "after_call": d.get("after_call"), "t_ms": d.get("t_ms"),
             "outcome": d.get("outcome"), "top3": ranked, "warmed": warm,
             "model_version": d.get("model_version"),
             "next_tool": nxt["tool_class"] if nxt else None}
        if nxt:
            e["top1_right"] = bool(ranked) and ranked[0]["tool_class"] == nxt["tool_class"]
            e["warmed_right"] = nxt["tool_class"] in warm
            e["next_cold"] = nxt.get("cold")
        out.append(e)
    return out


class Conversation:
    def __init__(self, task: Task, llm: Any, stacks: dict[str, Any], poller: Any, publish,
                 settings: dict[str, Any], clock=time.monotonic, mcp_factory=None):
        self.task, self.llm, self.stacks, self.poller = task, llm, stacks, poller
        self.publish, self.settings, self.clock = publish, settings, clock
        self.id = uuid.uuid4().hex[:12]
        self.sid = uuid.uuid4().hex
        make = mcp_factory or (lambda url: McpClient(url, timeout_s=180))
        self.clients = {k: make(st.s.mcp_url) for k, st in stacks.items()}
        for c in self.clients.values():
            c.session_id = self.sid
        self.messages: list[dict[str, Any]] = [{"role": "system", "content": task.system}]
        self.turn = 0
        self.reply = 0
        self.calls: list[dict[str, Any]] = []
        self.args: dict[int, dict[str, Any]] = {}   # in memory only (replay); never exported
        self.lock = asyncio.Lock()
        self.t0 = clock()
        self.idle0 = {k: dict(v) for k, v in poller.idle.items()}

    def idle_delta(self) -> dict[str, dict[str, float]]:
        return {k: {t: max(0.0, v - self.idle0.get(k, {}).get(t, 0.0))
                    for t, v in self.poller.idle.get(k, {}).items()} for k in self.stacks}

    async def _call_one(self, key: str, cls: str, args: dict[str, Any]):
        t0 = self.clock()
        res, info = await asyncio.to_thread(self.clients[key].call_tool, cls, args)
        return res, info, t0, self.clock()

    async def mirror(self, cls: str, args: dict[str, Any]) -> dict[str, tuple]:
        if self.settings["mirror"] == "sequential":
            pred = await self._call_one("pred", cls, args)
            base = await self._call_one("base", cls, args)
        else:
            base, pred = await asyncio.gather(self._call_one("base", cls, args),
                                              self._call_one("pred", cls, args))
        return {"base": base, "pred": pred}

    async def predictor_refresh(self, delay_s: float = 0.4) -> None:
        await asyncio.sleep(delay_s)   # the gateway asks the predictor off the call path
        try:
            sess = await self.stacks["pred"].session(self.sid)
        except Exception:  # noqa: BLE001
            sess = None
        if sess:
            await self.publish({"type": "predictor", "conversation": self.id,
                                "decisions": evaluate_decisions(sess)[-10:]})

    async def send_hint(self) -> None:
        t = self.clock()
        try:
            status = await self.stacks["pred"].hint(self.sid, self.turn)
        except Exception as e:  # noqa: BLE001
            status = f"error {type(e).__name__}"
        await self.publish({"type": "hint", "turn": self.turn, "status": status,
                            "t_s": round(t - self.t0, 2)})
        asyncio.create_task(self.predictor_refresh())

    async def tool_call(self, tc, think_ms: float, pos: int, replay: bool = False,
                        from_text: bool = False) -> dict[str, Any]:
        task = self.task
        rec: dict[str, Any] = {
            "conversation": self.id, "turn": self.turn, "reply": self.reply,
            "pos_in_reply": pos, "replay": replay, "llm_model": getattr(self.llm, "model", ""),
            "think_ms": round(think_ms, 1) if pos == 0 else 0.0,
            "tool_call_from_text": from_text, "tool": tc.name,
            "mirror_mode": self.settings["mirror"], "hint": self.settings["hint"],
            "baseline_policy": self.settings["baseline_policy"], "profile": task.profile}
        if tc.name not in task.tool_names:
            rec.update(tool_class=None, server=None, results_match=None,
                       error="unknown tool (not sent)")
            return {"rec": rec, "args": {}, "content": json.dumps({"error": "unknown tool"})}
        allowed = task.params(tc.name)
        args = {a: v for a, v in tc.arguments.items() if a in allowed}
        cls, gargs = task.gateway_call(tc.name, args)
        await self.publish({"type": "call_start", "turn": self.turn, "reply": self.reply,
                            "pos": pos, "tool": tc.name, "tool_class": cls})
        got = await self.mirror(cls, gargs)
        for key, (_res, info, t0, t1) in got.items():
            c = self.poller.contention(t0, t1)
            rec.update({f"{key}_wall_ms": round(info.wall_ms, 1), f"{key}_added_ms":
                        info.added_ms, f"{key}_held_ms": info.held_ms, f"{key}_cold": info.cold,
                        f"{key}_predicted": info.predicted,
                        f"{key}_warm_source": info.warm_source, f"{key}_ok": info.ok,
                        f"{key}_error": info.error,
                        f"{key}_contention_max_starting": c["max_starting"],
                        f"{key}_contention_samples": c["samples"],
                        f"{key}_t_s": round(t0 - self.t0, 3)})
        rec["server"] = self._server(cls)
        rec["tool_class"] = cls
        rec["results_match"] = result_key(got["base"][0]) == result_key(got["pred"][0])
        res, info = got["pred"][0], got["pred"][1]
        return {"rec": rec, "args": args, "content": render_result(res, info.ok, info.error)}

    def _server(self, cls: str) -> str:
        tm = yaml.safe_load((ROOT / "tools" / "tool_map.yaml").read_text())
        for s, spec in tm["servers"].items():
            if cls in spec["classes"]:
                return s
        return tm["fallback"]

    async def _emit_call(self, rec: dict[str, Any], args: dict[str, Any]) -> None:
        self.args[len(self.calls)] = args
        self.calls.append(rec)
        await self.publish({"type": "call", "call": rec})
        await self.publish({"type": "totals", "totals": totals(self.calls, self.idle_delta(),
                                                               pricing())})
        asyncio.create_task(self.predictor_refresh())

    async def user_turn(self, text: str, source: str = "presenter") -> None:
        async with self.lock:
            self.turn += 1
            await self.publish({"type": "turn_start", "turn": self.turn, "source": source,
                                "text": text, "hint": self.settings["hint"]})
            if self.settings["hint"]:
                await self.send_hint()
            self.messages.append({"role": "user", "content": text})
            for _ in range(MAX_STEPS):
                await self.publish({"type": "llm_start", "turn": self.turn})

                async def on_text(delta: str) -> None:
                    await self.publish({"type": "llm_delta", "turn": self.turn, "text": delta})
                try:
                    r = await self.llm.reply(self.messages, self.task.tools, on_text)
                except LLMError as e:
                    await self.publish({"type": "error", "where": "llm", "detail": str(e)[:200]})
                    break
                self.messages.append(r.message)
                await self.publish({"type": "llm_reply", "turn": self.turn,
                                    "reply": self.reply + 1 if r.tool_calls else None,
                                    "think_ms": round(r.think_ms, 1),
                                    "first_token_ms": r.first_token_ms,
                                    "n_calls": len(r.tool_calls), "text": r.content,
                                    "from_text": getattr(r, "from_text", False)})
                if not r.tool_calls:
                    break          # the agent answered or asked: the human's turn
                self.reply += 1
                for pos, tc in enumerate(r.tool_calls):
                    out = await self.tool_call(tc, r.think_ms, pos,
                                               from_text=getattr(r, "from_text", False))
                    self.messages.append({"role": "tool", "tool_call_id": tc.id,
                                          "content": out["content"]})
                    await self._emit_call(out["rec"], out["args"])
            await self.publish({"type": "turn_end", "turn": self.turn})

    async def replay(self, calls: list[dict[str, Any]]) -> None:
        """Replay a recorded conversation's tool calls on both stacks with the RECORDED gaps
        (the measured time between the end of one call and the start of the next on the
        predictive stack). No LLM is involved; turn-start hints are re-sent if enabled."""
        from agent.llm import ToolCall

        async with self.lock:
            for c in calls:
                if c["turn"] != self.turn:
                    self.turn = c["turn"]
                    await self.publish({"type": "turn_start", "turn": self.turn,
                                        "source": "replay", "text": "(replay)",
                                        "hint": self.settings["hint"]})
                    if self.settings["hint"]:
                        await self.send_hint()
                await asyncio.sleep(c["_gap_s"])     # the measured gap, not an invented one
                if c["pos_in_reply"] == 0:
                    self.reply += 1
                tc = ToolCall("r", c["tool"], c.get("_args", {}), "{}")
                out = await self.tool_call(tc, c["think_ms"], c["pos_in_reply"], replay=True)
                await self._emit_call(out["rec"], out["args"])
            await self.publish({"type": "turn_end", "turn": self.turn})

    def close(self) -> None:
        for c in self.clients.values():
            c.close()


def replay_plan(calls: list[dict[str, Any]], args: dict[int, dict[str, Any]]
                ) -> list[dict[str, Any]]:
    """Recorded calls with the measured gap before each one (predictive stack clock)."""
    out = []
    prev_end = None
    for i, c in enumerate(calls):
        if c.get("tool_class") is None or c.get("pred_t_s") is None:
            continue
        start = c["pred_t_s"]
        gap = 0.0 if prev_end is None else max(0.0, start - prev_end)
        out.append({**c, "_gap_s": round(gap, 3), "_args": args.get(i, {})})
        prev_end = start + (c.get("pred_wall_ms") or 0.0) / 1000.0
    return out


def to_csv(calls: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_FIELDS, extrasaction="ignore")
    w.writeheader()
    w.writerows(calls)
    return buf.getvalue()
