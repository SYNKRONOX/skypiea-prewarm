"""Mirrored conversation engine of the Live Console.

One conversation = one MCP session id, used on BOTH gateways. Every tool call the LLM makes is
sent to the predictive stack and to the baseline stack with the same arguments. The
conversation runs on the PREDICTIVE stack (the LLM sees its result); the baseline repeats each
call in its own lane with the SAME GAP: a baseline call is issued exactly as long after the
baseline's previous call ended as the predictive call was after the predictive stack's previous
call ended (the LLM think time, and between turns the human's time). So neither stack gets
extra lead time from the other being slower. Both gaps are recorded per call.
  concurrent (default): the baseline lane runs alongside the conversation - most visual; the
                        two stacks' pod starts can compete for the node (recorded per call as
                        start contention);
  sequential:           the predictive stack runs the turn live; when the turn ends the
                        baseline repeats that turn's calls (same gaps; a turn's first baseline
                        call can come later than its gap, recorded) before the next turn may
                        start - no overlap between the two stacks' calls.
The mock tools are deterministic, so both results should be identical; any difference is
flagged.

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
import re
import time
import uuid
from pathlib import Path
from typing import Any

import yaml

from agent.llm import LLMError
from agent.loop import render_result
from agent.mcp_client import McpClient
from agent.tasks import Task
from console import truth

ROOT = Path(__file__).resolve().parent.parent
MAX_STEPS = 8
CSV_FIELDS = ["conversation", "turn", "reply", "pos_in_reply", "replay", "llm_model",
              "think_ms", "tool_call_from_text", "tool", "tool_class", "server", "mirror_mode",
              "hint", "hint_mode", "lookahead", "console_map", "note",
              "baseline_policy", "profile", "results_match"] + [
    f"{s}_{f}" for s in ("base", "pred") for f in (
        "gap_ms", "wall_ms", "added_ms", "held_ms", "cold", "predicted", "warm_source", "ok",
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


def llm_content(res: Any, info: Any) -> str:
    """What the LLM sees. On a tool error, the tool's own message (e.g. a validation error),
    so the model can correct its arguments. Conversation only: never recorded or exported."""
    if not info.ok and isinstance(res, dict):
        text = " ".join(c.get("text", "") for c in res.get("content", []) if isinstance(c, dict))
        if text:
            return json.dumps({"error": text[:500]})
    return render_result(res, info.ok, info.error)


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


class BaseLane:
    """The baseline stack's own timeline (see the module docstring). ``gate`` holds the lane
    while it is cleared (sequential mode: during the predictive turn)."""

    def __init__(self, call_fn, clock):
        self.call_fn, self.clock = call_fn, clock
        self.q: asyncio.Queue = asyncio.Queue()
        self.gate = asyncio.Event()
        self.gate.set()
        self.last_end: float | None = None
        self.task = asyncio.create_task(self.run())

    def submit(self, cls: str, args: dict[str, Any], gap_s: float | None) -> asyncio.Future:
        fut = asyncio.get_running_loop().create_future()
        self.q.put_nowait((cls, args, gap_s, fut))
        return fut

    async def run(self) -> None:
        while True:
            cls, args, gap_s, fut = await self.q.get()
            try:
                await self.gate.wait()
                if self.last_end is not None and gap_s is not None:
                    wait = self.last_end + gap_s - self.clock()
                    if wait > 0:
                        await asyncio.sleep(wait)
                res, info, t0, t1 = await self.call_fn("base", cls, args)
                gap = None if self.last_end is None else t0 - self.last_end
                self.last_end = t1
                fut.set_result((res, info, t0, t1, gap))
            except Exception as e:  # noqa: BLE001 - delivered to the waiting call record
                if not fut.done():
                    fut.set_exception(e)
            finally:
                self.q.task_done()

    async def drain(self) -> None:
        await self.q.join()


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
        self.pred_last_end: float | None = None
        self._lane: BaseLane | None = None
        self._pending: list[asyncio.Task] = []
        self._next_seq = 0                            # call order in the conversation
        # turn-start hints (mode off | enter | typing): when each hint was sent, per turn
        self.hint_log: dict[int, list[dict[str, Any]]] = {}
        self.typing_t: float | None = None            # first keystroke of the coming turn
        self.typing_hinted: set[int] = set()           # turns that got their typing hint
        self.turns: list[dict[str, Any]] = []          # per-turn records (lead time, 1st call)
        # Skypiea Air: the conversation's own mock database session (writes persist here)
        self.backend = None
        if task.scenario == "skyair":
            from agent.skyair.tools import Session
            self.backend = Session()
        self.writes: list[dict[str, Any]] = []         # truth panel: real write attempts
        self.truth: list[dict[str, Any]] = []          # truth panel: claim checks per turn
        self.abandoned: list[dict[str, Any]] = []      # typing hints whose turn never came
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

    def lane(self) -> BaseLane:
        loop = asyncio.get_running_loop()
        if self._lane is None or self._lane.task.get_loop() is not loop:
            self._lane = BaseLane(self._call_one, self.clock)
        return self._lane

    async def end_of_turn(self) -> None:
        """Let the baseline lane finish this turn's calls (sequential: start it now), and wait
        for every call record of the turn."""
        lane = self.lane()
        lane.gate.set()
        await lane.drain()
        if self.settings["mirror"] == "sequential":
            lane.gate.clear()
        pending, self._pending = self._pending, []
        await asyncio.gather(*pending)

    async def predictor_refresh(self, delay_s: float = 0.4) -> None:
        await asyncio.sleep(delay_s)   # the gateway asks the predictor off the call path
        try:
            sess = await self.stacks["pred"].session(self.sid)
        except Exception:  # noqa: BLE001
            sess = None
        if sess:
            await self.publish({"type": "predictor", "conversation": self.id,
                                "decisions": evaluate_decisions(sess)[-10:]})

    async def send_hint(self, turn: int | None = None, source: str = "enter") -> None:
        """Metadata-only turn hint to the PREDICTIVE gateway (session id + turn number)."""
        turn = self.turn if turn is None else turn
        t = self.clock()
        try:
            status = await self.stacks["pred"].hint(self.sid, turn)
        except Exception as e:  # noqa: BLE001
            status = f"error {type(e).__name__}"
        self.hint_log.setdefault(turn, []).append({"source": source, "t": t, "status": status})
        await self.publish({"type": "hint", "turn": turn, "status": status, "source": source,
                            "t_s": round(t - self.t0, 2)})
        asyncio.create_task(self.predictor_refresh())

    def hint_mode(self) -> str:
        return self.settings.get("hint_mode") or ("enter" if self.settings.get("hint") else "off")

    async def open_hint(self) -> None:
        """Typing mode: a hint for turn 1 as soon as the conversation opens."""
        if self.hint_mode() == "typing" and self.turn == 0:
            await self.send_hint(1, "open")

    async def typing_started(self) -> None:
        """First keystroke of the coming turn (the browser sends it once per turn)."""
        if self.typing_t is None:
            self.typing_t = self.clock()
        nxt = self.turn + 1
        if self.hint_mode() == "typing" and nxt not in self.typing_hinted:
            self.typing_hinted.add(nxt)
            await self.send_hint(nxt, "typing")

    def typing_cancelled(self) -> dict[str, Any] | None:
        """The input was emptied without sending: the coming turn's typing hint (if any) is
        abandoned. Returns what to measure (the caller measures its idle pod-seconds)."""
        nxt = self.turn + 1
        self.typing_t = None
        hs = [h for h in self.hint_log.get(nxt, []) if h["source"] in ("typing", "open")]
        if not hs:
            return None
        return {"turn": nxt, "hint_t": hs[0]["t"], "session": self.sid}

    async def tool_call(self, tc, think_ms: float, pos: int, replay: bool = False,
                        from_text: bool = False) -> dict[str, Any]:
        task = self.task
        rec: dict[str, Any] = {
            "conversation": self.id, "turn": self.turn, "reply": self.reply,
            "pos_in_reply": pos, "replay": replay, "llm_model": getattr(self.llm, "model", ""),
            "think_ms": round(think_ms, 1) if pos == 0 else 0.0,
            "tool_call_from_text": from_text, "tool": tc.name,
            "mirror_mode": self.settings["mirror"], "hint": self.hint_mode() != "off",
            "hint_mode": self.hint_mode(), "lookahead": self.settings.get("lookahead", 1),
            "console_map": bool(self.settings.get("console_map")),
            "baseline_policy": self.settings["baseline_policy"], "profile": task.profile}
        unknown = tc.name not in task.tool_names
        if unknown:
            # still sent through both gateways (class "other": outside the vocabulary, so the
            # predictor abstains); the LLM is told the tool does not exist
            args, cls, gargs = {}, "other", {}
            rec["note"] = "unknown tool: no prediction, normal behaviour"
        else:
            allowed = task.params(tc.name)
            raw = tc.arguments
            if hasattr(task, "normalize_args"):        # documented argument synonyms (Skye)
                raw = task.normalize_args(tc.name, raw)
            # null = "not given" (small models send null for optional parameters)
            args = {a: v for a, v in raw.items() if a in allowed and v is not None}
            cls, gargs = task.gateway_call(tc.name, args)
        await self.publish({"type": "call_start", "turn": self.turn, "reply": self.reply,
                            "pos": pos, "tool": tc.name, "tool_class": cls})
        lane = self.lane()
        if self.settings["mirror"] == "sequential":
            lane.gate.clear()
        # the baseline call is queued when the predictive call is ISSUED, with the gap the
        # predictive stack saw, so a slow predictive call cannot delay the baseline
        issue = self.clock()
        p_gap = None if self.pred_last_end is None else issue - self.pred_last_end
        fut = lane.submit(cls, gargs, p_gap)
        p_res, p_info, p0, p1 = await self._call_one("pred", cls, gargs)
        self.pred_last_end = p1
        rec["server"] = self._server(cls)
        rec["tool_class"] = cls

        def put(key: str, info: Any, t0: float, t1: float, gap: float | None) -> None:
            c = self.poller.contention(t0, t1)
            rec.update({f"{key}_gap_ms": None if gap is None else round(gap * 1000, 1),
                        f"{key}_wall_ms": round(info.wall_ms, 1), f"{key}_added_ms":
                        info.added_ms, f"{key}_held_ms": info.held_ms, f"{key}_cold": info.cold,
                        f"{key}_predicted": info.predicted,
                        f"{key}_warm_source": info.warm_source, f"{key}_ok": info.ok,
                        f"{key}_error": info.error,
                        f"{key}_contention_max_starting": c["max_starting"],
                        f"{key}_contention_samples": c["samples"],
                        f"{key}_t_s": round(t0 - self.t0, 3)})

        put("pred", p_info, p0, p1, p_gap)

        async def finish() -> dict[str, Any]:
            try:
                b_res, b_info, b0, b1, b_gap = await fut
                put("base", b_info, b0, b1, b_gap)
                rec["results_match"] = result_key(b_res) == result_key(p_res)
            except Exception as e:  # noqa: BLE001
                rec.update(base_ok=False, base_error=f"{type(e).__name__}"[:100],
                           results_match=None)
            return rec
        if unknown:
            content = json.dumps({"error": f"unknown tool {tc.name!r}; available: "
                                  f"{task.tool_names}"})
        elif self.backend is not None:       # Skypiea Air: answer from the session database
            result = self.backend.call(tc.name, args)
            content = json.dumps(result, default=str)[:3000]
            await self._log_write(tc.name, args, result)
        else:
            content = llm_content(p_res, p_info)
        return {"rec": rec, "args": args, "content": content, "finish": finish}

    async def _log_write(self, tool: str, args: dict[str, Any], result: Any) -> None:
        """Truth panel: record a real write attempt and publish the booking record."""
        if tool in truth.WRITES and isinstance(result, dict):
            outcome = truth.write_outcome(tool, result)
            self.writes.append({"turn": self.turn, "reply": self.reply, "tool": tool,
                                "kind": truth.WRITES[tool][0], "outcome": outcome,
                                "what": truth.describe(tool, args, result, outcome)})
        await self.publish({"type": "booking", "state": truth.state(self.backend),
                            "writes": self.writes[-12:]})

    def check_claims(self, text: str) -> dict[str, Any] | None:
        """Rule-based check of the final reply of a turn against this turn's real writes."""
        if self.backend is None:
            return None
        tw = [w for w in self.writes if w["turn"] == self.turn]
        ew = [w for w in self.writes if w["turn"] < self.turn]
        res = {"turn": self.turn, **truth.check(text, tw, ew)}
        prev = next((t for t in reversed(self.truth) if t.get("note_sent")), None)
        if prev is not None and prev.get("corrected") is None and prev["turn"] < self.turn:
            # did the reply after the system note correct itself? (heuristic, logged)
            prev["corrected"] = bool(re.search(r"\b(sorry|apolog|correct|mistake|not (?:yet )?"
                                               r"(?:been )?(?:changed|cancell?ed|refunded|"
                                               r"added|made))", text or "", re.I))
            for row in self.turns:
                if row["turn"] == prev["turn"]:
                    row["truth_corrected"] = prev["corrected"]
        if res["verdict"] == "not_done" and self.settings.get("truth_note", True):
            self.messages.append({"role": "system", "content": (
                "Booking system note: your last reply said something was done ("
                + ", ".join(res["claims"]) + ") but no such change succeeded. Tell the "
                "customer plainly in your next reply that it was NOT done.")})
            res["note_sent"] = True
            res["corrected"] = None
        self.truth.append(res)
        return res

    def _server(self, cls: str) -> str:
        tm = yaml.safe_load((ROOT / "tools" / "tool_map.yaml").read_text())
        for s, spec in tm["servers"].items():
            if cls in spec["classes"]:
                return s
        return tm["fallback"]

    def _track(self, out: dict[str, Any]) -> None:
        """Emit the call record once the baseline lane has repeated the call; slots are
        reserved now so records keep the conversation's order."""
        idx = self._next_seq
        self._next_seq += 1
        if "finish" not in out:          # not sent (unknown tool)
            self._pending.append(asyncio.create_task(self._emit_call(out["rec"], out["args"],
                                                                     idx)))
            return

        async def done() -> None:
            rec = await out["finish"]()
            await self._emit_call(rec, out["args"], idx)
        self._pending.append(asyncio.create_task(done()))

    async def _emit_call(self, rec: dict[str, Any], args: dict[str, Any],
                         idx: int | None = None) -> None:
        rec["seq"] = len(self.calls) if idx is None else idx
        self.args[rec["seq"]] = args
        self.calls.append(rec)
        self.calls.sort(key=lambda c: c["seq"])
        await self.publish({"type": "call", "call": rec})
        await self.publish({"type": "totals", "totals": totals(self.calls, self.idle_delta(),
                                                               pricing())})
        asyncio.create_task(self.predictor_refresh())

    async def user_turn(self, text: str, source: str = "presenter",
                        typing_ms: float | None = None) -> None:
        async with self.lock:
            self.turn += 1
            t_enter = self.clock()
            typing_ms = typing_ms if typing_ms is not None else (
                None if self.typing_t is None else (t_enter - self.typing_t) * 1000)
            self.typing_t = None
            mode = self.hint_mode()
            await self.publish({"type": "turn_start", "turn": self.turn, "source": source,
                                "text": text, "hint": mode != "off", "hint_mode": mode})
            # typing mode also re-sends at Enter: the lead-time runs showed typing can outlast
            # the 30 s warm window of the typing hint (docs/handoff/LEADTIME_REPORT.md)
            if mode in ("enter", "typing"):
                await self.send_hint(self.turn, "enter")
            n_calls_before = self._next_seq
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
                    tr = self.check_claims(r.content)
                    if tr is not None:
                        await self.publish({"type": "truth", "truth": tr})
                    break          # the agent answered or asked: the human's turn
                self.reply += 1
                for pos, tc in enumerate(r.tool_calls):
                    out = await self.tool_call(tc, r.think_ms, pos,
                                               from_text=getattr(r, "from_text", False))
                    self.messages.append({"role": "tool", "tool_call_id": tc.id,
                                          "content": out["content"]})
                    self._track(out)
            if self.settings["mirror"] == "sequential":
                await self.publish({"type": "status_turn", "turn": self.turn,
                                    "text": "baseline repeating this turn's calls (same gaps)"})
            await self.end_of_turn()
            self._record_turn(source, mode, t_enter, typing_ms, n_calls_before)
            await self.publish({"type": "turn_end", "turn": self.turn})

    def _record_turn(self, source: str, mode: str, t_enter: float, typing_ms: float | None,
                     first_seq: int) -> None:
        hints = self.hint_log.get(self.turn, [])
        # hints of this turn sent before the LLM was asked (Enter-mode hints go out right
        # after Enter: lead 0); lead = first such hint -> Enter
        early = [h for h in hints if h["t"] <= t_enter + 1.0]
        first = next((c for c in self.calls if c["seq"] >= first_seq and c.get("tool_class")),
                     None)
        row = {"conversation": self.id, "turn": self.turn, "source": source,
               "hint_mode": mode, "hint_sources": "+".join(h["source"] for h in hints),
               "lead_ms": round(max(0.0, t_enter - early[0]["t"]) * 1000, 1) if early else None,
               "typing_ms": None if typing_ms is None else round(typing_ms, 1),
               "lookahead": self.settings.get("lookahead", 1),
               "console_map": bool(self.settings.get("console_map")),
               "first_tool": first["tool"] if first else None,
               "calls": self._next_seq - first_seq}
        tr = next((t for t in reversed(self.truth) if t["turn"] == self.turn), None)
        if tr is not None:
            row.update(truth_claims="+".join(tr["claims"]), truth_verdict=tr["verdict"],
                       truth_badge=" | ".join(tr["badges"]),
                       truth_note_sent=bool(tr.get("note_sent")))
        if first:
            for k in ("base", "pred"):
                row.update({f"{k}_first_wall_ms": first.get(f"{k}_wall_ms"),
                            f"{k}_first_cold": first.get(f"{k}_cold")})
            row["pred_first_predicted"] = first.get("pred_predicted")
            row["pred_first_warm_source"] = first.get("pred_warm_source")
            row["pred_first_warm"] = first.get("pred_cold") is False
        self.turns.append(row)
        asyncio.create_task(self.publish({"type": "turn", "turn_record": row}))

    async def replay(self, calls: list[dict[str, Any]]) -> None:
        """Replay a recorded conversation's tool calls on both stacks with the RECORDED gaps
        (the measured time between the end of one call and the start of the next on the
        predictive stack). No LLM is involved; turn-start hints are re-sent if enabled."""
        from agent.llm import ToolCall

        async with self.lock:
            for c in calls:
                if c["turn"] != self.turn:
                    if self.turn:
                        await self.end_of_turn()
                        await self.publish({"type": "turn_end", "turn": self.turn})
                    self.turn = c["turn"]
                    await self.publish({"type": "turn_start", "turn": self.turn,
                                        "source": "replay", "text": "(replay)",
                                        "hint": self.hint_mode() != "off"})
                    if self.hint_mode() != "off":
                        await self.send_hint(self.turn, "replay")
                await asyncio.sleep(c["_gap_s"])     # the measured gap, not an invented one
                if c["pos_in_reply"] == 0:
                    self.reply += 1
                tc = ToolCall("r", c["tool"], c.get("_args", {}), "{}")
                out = await self.tool_call(tc, c["think_ms"], c["pos_in_reply"], replay=True)
                self._track(out)
            await self.end_of_turn()
            await self.publish({"type": "turn_end", "turn": self.turn})

    def close(self) -> None:
        if self._lane is not None and not self._lane.task.done():
            self._lane.task.cancel()
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


TURN_FIELDS = ["conversation", "turn", "source", "hint_mode", "hint_sources", "lead_ms",
               "typing_ms", "lookahead", "console_map", "first_tool", "calls",
               "base_first_wall_ms", "base_first_cold", "pred_first_wall_ms", "pred_first_cold",
               "pred_first_predicted", "pred_first_warm_source", "pred_first_warm",
               "truth_claims", "truth_verdict", "truth_badge", "truth_note_sent",
               "truth_corrected"]


def to_csv(calls: list[dict[str, Any]], fields: list[str] | None = None) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields or CSV_FIELDS, extrasaction="ignore")
    w.writeheader()
    w.writerows(calls)
    return buf.getvalue()
