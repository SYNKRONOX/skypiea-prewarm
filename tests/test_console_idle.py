"""Summary idle pod-seconds (W1 P0 #1): the console's totals must follow the controllers'
idle counters between calls and after the last call, not only when a call record ends.
Regression: the summary said "Skypiea kept pods warm 0.0 s longer" while Skypiea pods were
warm, because totals were computed only at call ends (first call: counters not read yet)."""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("starlette")

from console.app import Console  # noqa: E402
from console.llm_stream import ScriptedStreamingLLM  # noqa: E402


class IdleStack:
    """Stack double whose controller idle counter is set by the test."""

    def __init__(self, key: str):
        from console.stacks import BASE, PRED
        self.s = {"base": BASE, "pred": PRED}[key]
        self.idle: dict[str, float] = {}

    async def state(self):
        return {"user_lookup": {"live": 0, "ready": 0, "queued": 0, "warm_for_s": 0}}

    async def idle_pod_s(self):
        return dict(self.idle)

    async def policy(self):
        return {"name": "scale_to_zero"}

    async def set_policy(self, key):
        return {}

    async def hint(self, sid, turn):
        return 202

    async def session(self, sid):
        return None


def _call() -> dict:
    return {"tool": "find_customer", "tool_class": "user_lookup", "turn": 1,
            "base_ok": True, "pred_ok": True, "base_wall_ms": 2600.0, "pred_wall_ms": 40.0,
            "base_cold": True, "pred_cold": False, "base_added_ms": 2500, "pred_added_ms": 0}


def test_idle_accrued_after_the_last_call_reaches_the_summary() -> None:
    base, pred = IdleStack("base"), IdleStack("pred")
    base.idle, pred.idle = {"user": 100.0}, {"user": 500.0}       # earlier conversations
    c = Console(ScriptedStreamingLLM([]), base=base, pred=pred, start_poller=False)
    events: list = []

    async def go():
        async def pub(ev):
            events.append(ev)
        c.hub.publish = pub
        c.poller.publish = pub
        await c.poller.refresh_idle()
        conv = c.new_conversation()
        conv.calls.append(_call())          # the call ended before any idle was counted
        # pods stay warm after the call: the counters grow, no further call is made
        base.idle, pred.idle = {"user": 103.5}, {"user": 520.0, "flight": 7.0}
        c.poller._last_slow = -1e9
        await c.poller.tick()
        return conv

    conv = asyncio.run(go())
    tot = [e for e in events if e["type"] == "totals"]
    assert tot, "the slow poll must re-publish the conversation's totals"
    t = tot[-1]
    assert t["conversation"] == conv.id
    assert t["totals"]["pred"]["idle_pod_s"] == 27.0
    assert t["totals"]["base"]["idle_pod_s"] == 3.5
    assert t["totals"]["pred"]["idle_pod_s"] - t["totals"]["base"]["idle_pod_s"] > 0


def test_no_totals_before_the_first_call_and_reset_reads_fresh_counters() -> None:
    base, pred = IdleStack("base"), IdleStack("pred")
    c = Console(ScriptedStreamingLLM([]), base=base, pred=pred, start_poller=False)
    events: list = []

    async def go():
        async def pub(ev):
            events.append(ev)
        c.hub.publish = pub
        c.poller.publish = pub
        c.new_conversation()
        await c.publish_totals()                       # no calls yet: nothing to show
        assert not [e for e in events if e["type"] == "totals"]
        # stale poller value vs. the controller's real counter at Reset time
        pred.idle = {"user": 40.0}
        c.poller.all_zero = lambda: True
        await c.reset()
        return c.conv

    conv = asyncio.run(go())
    assert conv.idle0["pred"] == {"user": 40.0}       # idle before the Reset is not charged
