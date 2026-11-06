"""Truth panel: booking record, real write log, and the rule-based claim check."""
from __future__ import annotations

import asyncio

import pytest

from agent.skyair.tools import Session, personas
from console import truth


def _persona():
    return next(p for p in personas() if "booking_code" in p)


@pytest.mark.parametrize("text,kinds", [
    ("I've updated your flight to SA123.", ["change"]),
    ("Your booking has been cancelled and a refund of $420 has been issued.",
     ["cancel", "refund"]),
    ("I have added 2 bags to your booking.", ["baggage"]),
    ("Would you like me to change your flight to SA123?", []),
    ("I can cancel the booking if you confirm.", []),
    ("I'm sorry, the flight has not been changed.", []),
    ("Your booking ZKMD9U has two flights.", []),
])
def test_claims(text, kinds) -> None:
    assert truth.claims(text) == kinds


def test_check_and_state() -> None:
    w = [{"turn": 1, "kind": "change", "outcome": "refused"}]
    r = truth.check("Done! I've changed your flight.", w, [])
    assert r["verdict"] == "not_done" and r["badges"] == [truth.NOT_DONE["change"]]
    w.append({"turn": 1, "kind": "change", "outcome": "success"})
    assert truth.check("I've changed your flight.", w, [])["verdict"] == "agree"
    assert truth.check("Your flight has been changed.", [], w)["earlier"] == ["change"]
    s = Session()
    assert truth.state(s) == {"available": True, "verified": None, "bookings": []}
    p = _persona()
    s.verify_customer(email=p["email"])
    st = truth.state(s)
    assert st["verified"]["name"] == p["name"]
    assert p["booking_code"] in [b["booking_code"] for b in st["bookings"]]


def test_truth_in_the_engine_agree_disagree_and_correction() -> None:
    pytest.importorskip("starlette")
    from agent.tasks import get
    from console.engine import TURN_FIELDS, Conversation, to_csv
    from console.llm_stream import ScriptedStreamingLLM
    from tests.test_console import FakeGW, FakeStack, NullPoller, factory

    p = _persona()
    gws = {"base": FakeGW("base"), "pred": FakeGW("pred")}
    events: list[dict] = []

    async def publish(ev):
        events.append(ev)
    code = p["booking_code"]
    llm = ScriptedStreamingLLM(
        [[("verify_customer", {"email": p["email"]}),
          ("add_baggage", {"booking_code": code, "bags": 1, "customer_confirmed": True})],
         [("change_flight", {"booking_code": code, "old_flight_no": "SA1",
                             "new_flight_no": "SA2", "customer_confirmed": True})],
         []],
        answers=["I've added 1 bag to your booking.",
                 "Great news, your flight has been changed to SA2.",
                 "Sorry, I made a mistake: your flight was not changed."])
    conv = Conversation(get("skyair"), llm, {"base": FakeStack("base"),
                                             "pred": FakeStack("pred")}, NullPoller(),
                        publish, {"mirror": "concurrent", "hint_mode": "off",
                                  "baseline_policy": "scale_to_zero"}, mcp_factory=factory(gws))
    before = [m["content"] for m in conv.messages]
    for t in ("hi", "change my flight", "really?"):
        asyncio.run(conv.user_turn(t))
    t1, t2, t3 = conv.truth
    assert t1["verdict"] == "agree" and t1["claims"] == ["baggage"]
    assert t2["verdict"] == "not_done"
    assert t2["badges"] == ["Not done: the booking was not changed"]
    assert t2["note_sent"] is True and t2["corrected"] is True
    assert t3["verdict"] == "no_claim"
    assert [w["outcome"] for w in conv.writes] == ["success", "refused"]
    # Skye's text is never edited
    said = [m["content"] for m in conv.messages if m.get("role") == "assistant"
            and m.get("content")]
    assert said[1] == "Great news, your flight has been changed to SA2."
    assert any(m.get("role") == "system" and "NOT done" in m["content"]
               for m in conv.messages[len(before):])
    csv = to_csv(conv.turns, TURN_FIELDS)
    assert "Not done: the booking was not changed" in csv and "truth_verdict" in csv
    assert any(e["type"] == "truth" for e in events)
    assert any(e["type"] == "booking" for e in events)


def test_booking_endpoint_and_chat_view() -> None:
    pytest.importorskip("starlette")
    from starlette.testclient import TestClient

    from console.app import Console, chat_view, make_app
    from console.llm_stream import ScriptedStreamingLLM
    from tests.test_console import FakeStack

    c = Console(ScriptedStreamingLLM([]), base=FakeStack("base"), pred=FakeStack("pred"),
                start_poller=False)
    with TestClient(make_app(c)) as cl:
        r = cl.get("/api/skyair/state").json()
        assert r["writes"] == [] and r["truth"] == []
        assert "Booking record" in cl.get("/").text
    v = chat_view([{"role": "system", "content": "x"}, {"role": "user", "content": "hi"},
                   {"role": "assistant", "content": ""}, {"role": "tool", "content": "{}"},
                   {"role": "assistant", "content": "Hello"}])
    assert v == [{"role": "user", "text": "hi", "turn": 1},
                 {"role": "assistant", "text": "Hello", "turn": 1}]
