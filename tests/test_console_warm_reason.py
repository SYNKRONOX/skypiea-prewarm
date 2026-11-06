"""Warm-but-not-predicted calls (W1 P0 #4): the console names the real cause from the
gateway's session record and never implies a prediction; a kill before the first turn
starts the conversation again from 0 pods."""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("starlette")

from console.engine import warm_reason  # noqa: E402

# the predictor-kill demo run (phaseb_change_kill): the open hint warmed user_lookup at 0.9 s
# before the kill took effect, later decisions failed (ConnectError)
SESSION = {
    "decisions": [
        {"trigger": "turn_start", "turn": 1, "t_ms": 900.0, "outcome": "ok",
         "decisions": [{"tool_class": "user_lookup", "warm": True, "copies": 1}]},
        {"trigger": "turn_start", "turn": 1, "t_ms": 14000.0, "outcome": "error:ConnectError",
         "decisions": []},
        {"after_call": 0, "t_ms": 17100.0, "outcome": "error:ConnectError"}],
    "calls": [
        {"tool_class": "user_lookup", "server": "user", "t_start_ms": 17000.0,
         "t_end_ms": 17040.0},
        {"tool_class": "user_lookup", "server": "user", "t_start_ms": 29000.0,
         "t_end_ms": 29035.0},
        {"tool_class": "flight_search", "server": "search", "t_start_ms": 40000.0,
         "t_end_ms": 42600.0}]}


def test_first_warm_call_after_a_kill_is_attributed_to_the_earlier_hint() -> None:
    r = warm_reason(SESSION, 0, "user", "user_lookup")
    assert r["reason"] == "earlier_decision" and r["trigger"] == "turn_start"
    assert r["since_ms"] == 16100


def test_second_warm_call_is_kept_after_use() -> None:
    r = warm_reason(SESSION, 1, "user", "user_lookup")
    assert r["reason"] == "kept_after_use" and r["since_ms"] == 11960


def test_nothing_in_the_conversation_means_already_up_and_mismatch_is_unknown() -> None:
    assert warm_reason(SESSION, 2, "search", "flight_search")["reason"] == "already_up"
    assert warm_reason(SESSION, 2, "search", "user_lookup")["reason"] == "unknown"
    assert warm_reason(None, 0, "user", "user_lookup")["reason"] == "unknown"
    assert "predicted" not in {warm_reason(SESSION, i, "user", "user_lookup")["reason"]
                               for i in range(3)}


def test_kill_before_the_first_turn_resets_the_conversation(monkeypatch) -> None:
    from console import app as appmod
    from console.app import Console
    from console.llm_stream import ScriptedStreamingLLM
    from tests.test_console_idle import IdleStack

    c = Console(ScriptedStreamingLLM([]), base=IdleStack("base"), pred=IdleStack("pred"),
                start_poller=False)

    async def kubectl(*a, **k):
        return 0, "scaled"
    monkeypatch.setattr(appmod, "kubectl", kubectl)

    async def health():
        return {"status": "unreachable"}
    c.model_health = health

    async def go():
        async def pub(ev):
            pass
        c.hub.publish = pub
        c.poller.all_zero = lambda: True
        first = c.new_conversation()
        await c.predictor("kill")
        assert c.conv is not first                     # started again from 0 pods
        second = c.conv
        second.turn = 2                                # mid-conversation kill: kept
        await c.predictor("kill")
        assert c.conv is second
    asyncio.run(go())


def test_a_decision_for_another_class_on_the_same_server_counts() -> None:
    sess = {"decisions": [{"trigger": "turn_start", "turn": 2, "t_ms": 100.0, "outcome": "ok",
                           "decisions": [{"tool_class": "reservation_lookup", "warm": True,
                                          "copies": 1}]}],
            "calls": [{"tool_class": "reservation_book", "server": "travel_booking",
                       "t_start_ms": 900.0, "t_end_ms": 950.0}]}
    srv = {"reservation_lookup": "travel_booking", "reservation_book": "travel_booking"}.get
    assert warm_reason(sess, 0, "travel_booking", "reservation_book")["reason"] == "already_up"
    r = warm_reason(sess, 0, "travel_booking", "reservation_book", srv)
    assert r["reason"] == "earlier_decision" and r["since_ms"] == 800
