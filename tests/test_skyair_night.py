"""W1 night: synthetic India-centred world, place/date resolution, STATE block, tool gating,
steering errors, argument validation with retry and auto-transfer, explicit confirmation,
duplicate-write guard, and the scenario suite's end-state oracle. Offline."""

from __future__ import annotations

import asyncio

import pytest

from agent.skyair import world as W
from agent.skyair.tools import Session, resolve_airport, resolve_date
from console import guards


@pytest.mark.parametrize("text,code", [("Bangalore", "BLR"), ("Bengaluru", "BLR"),
                                       ("blr", "BLR"), ("Bengaluru (BLR)", "BLR"),
                                       ("bangalor", "BLR"), ("Bombay", "BOM"),
                                       ("new delhi", "DEL"), ("Trivandrum", "TRV"),
                                       ("Paris", None), ("", None)])
def test_city_resolution(text, code) -> None:
    assert resolve_airport(text) == code


@pytest.mark.parametrize("text,iso", [("tomorrow", "2027-01-12"), ("next Friday", "2027-01-15"),
                                      ("friday", "2027-01-15"), ("20 Jan", "2027-01-20"),
                                      ("Jan 20", "2027-01-20"), ("in 3 days", "2027-01-14"),
                                      ("Wednesday 13 January", "2027-01-13"),
                                      ("2027-02-03", "2027-02-03"), ("someday", None)])
def test_relative_dates_use_the_demo_world_today(text, iso) -> None:
    assert W.ANCHOR.isoformat() == "2027-01-11"
    assert resolve_date(text) == iso


def test_world_shape_hubs_airports_and_rules() -> None:
    w = W.build()
    codes = set(w["airports"])
    for c in ("DEL", "BOM", "BLR", "HYD", "MAA", "CCU", "GOI", "IXZ", "IMF", "DXB", "LHR"):
        assert c in codes
    assert sum(1 for a in w["airports"].values() if not a["international"]) >= 35
    pairs = {(f["origin"], f["destination"]) for f in w["flights"]}
    assert all((a, b) in pairs for a in W.HUBS for b in W.HUBS if a != b)
    assert W.block_minutes("DEL", "BOM") == 115
    f = next(x for x in w["flights"] if x["origin"] == "DEL" and x["destination"] == "BOM")
    assert W.fare_price(f["price"], "Business") == int(round(f["price"] * 3.0 / 10) * 10)


def test_gating_and_steering_messages() -> None:
    s = Session()
    assert s.allowed_tools() == {"find_customer", "verify_customer", "search_flights",
                                 "list_offers", "transfer_to_human"}
    assert "search_flights(" in s.why_not("book_flight")
    assert "verify_customer(email=" in s.why_not("apply_promo")
    r = s.verify_customer(email="nobody@example.com")
    assert r["error"] == "email_not_found" and "book_flight(flight_id, fare, name, email)" in \
        r["message"]
    s.search_flights("Bangalore", "Goa", "next friday")
    assert "book_flight" in s.allowed_tools() and len(s.last_search) <= 5
    s.verify_customer(email="dev.singh@example.com")         # one active booking: selected
    assert {"cancel_booking", "apply_promo", "change_flight"} <= s.allowed_tools()
    st = s.state_block()
    assert "Demo-world today: Monday 11 January" in st and "Dev Singh" in st
    assert "SKY" in st and "Selected booking: VPGX3T" in st


def test_validation_coerces_string_booleans_and_flags_bad_arguments() -> None:
    n = {"name": "Nova Quill", "email": "None", "booking_code": "null"}
    assert guards.validate("find_customer", n) == [] and n == {"name": "Nova Quill"}
    a = {"booking_code": "VPGX3T", "bags": "1", "customer_confirmed": "false"}
    assert guards.validate("add_baggage", a) == [] and a["customer_confirmed"] is False \
        and a["bags"] == 1
    p = guards.validate("book_flight", {"flight_no": "SKY1-99XXX", "fare": "first",
                                        "email": "x@"}, Session())
    assert len(p) == 3
    assert guards.confirmed_ok("cancel_booking", "Yes, please cancel it.", None)
    assert not guards.confirmed_ok("cancel_booking", "I want to cancel.", None)
    assert guards.confirmed_ok("change_flight", "yes", {"tool": "change_flight"})
    assert not guards.confirmed_ok("change_flight", "no, wait", {"tool": "change_flight"})


def _conv(turns, answers=None):
    from agent.suite.run import NullMcp, NullPoller, NullStack
    from agent.tasks import get
    from console.engine import Conversation
    from console.llm_stream import ScriptedStreamingLLM
    events = []

    async def publish(ev):
        events.append(ev)
    llm = ScriptedStreamingLLM(turns, answers=answers)
    conv = Conversation(get("skyair"), llm, {"base": NullStack("base"),
                                             "pred": NullStack("pred")}, NullPoller(), publish,
                        {"mirror": "concurrent", "hint_mode": "off",
                         "baseline_policy": "scale_to_zero"}, mcp_factory=lambda u: NullMcp())
    return conv, llm, events


def test_engine_offers_only_allowed_tools_and_reinjects_state() -> None:
    pytest.importorskip("starlette")
    conv, llm, _ = _conv([[("search_flights", {"origin": "Bangalore", "destination": "Goa",
                                               "date": "next friday"})]])
    seen = []
    orig = llm.reply

    async def spy(messages, tools, on_text=None):
        seen.append(({t["function"]["name"] for t in tools}, messages[0]["content"]))
        return await orig(messages, tools, on_text)
    llm.reply = spy
    asyncio.run(conv.user_turn("Hi, Bangalore to Goa next Friday please"))
    assert "book_flight" not in seen[0][0] and "book_flight" in seen[1][0]
    assert "STATE (booking system, now)" in seen[1][1] and "Last search BLR->GOI" in seen[1][1]


def test_invalid_twice_transfers_to_a_human_and_confirmation_is_enforced() -> None:
    pytest.importorskip("starlette")
    bad = ("book_flight", {"flight_no": "SKY9999-01JAN"})
    conv, _, events = _conv([[("search_flights", {"origin": "BLR", "destination": "GOI",
                                                  "date": "next friday"}), bad, bad]])
    asyncio.run(conv.user_turn("book me something"))
    assert [g["kind"] for g in conv.guards] == ["invalid_retry", "auto_transfer"]
    assert conv.backend.transferred and any(e["type"] == "guard" for e in events)

    fid = "SKY142-15JAN"
    conv, llm, _ = _conv([[("search_flights", {"origin": "BLR", "destination": "GOI",
                                               "date": "next friday"}),
                           ("book_flight", {"flight_no": fid, "name": "Nova Quill",
                                            "email": "nova@example.org",
                                            "customer_confirmed": "true"})],
                          [("book_flight", {"flight_no": fid, "name": "Nova Quill",
                                            "email": "nova@example.org",
                                            "customer_confirmed": True}),
                           ("book_flight", {"flight_no": fid, "customer_confirmed": True})]])
    asyncio.run(conv.user_turn("Bangalore to Goa next Friday, I'm Nova Quill, nova@example.org"))
    assert conv.guards[-1]["kind"] == "confirm_first" and not conv.backend.new_bookings
    assert conv.backend.pending["tool"] == "book_flight"
    asyncio.run(conv.user_turn("Yes, go ahead."))
    assert len(conv.backend.new_bookings) == 1                      # booked once
    assert conv.guards[-1]["kind"] == "confirm_first"               # the repeat was stopped


def test_suite_has_18_cases_and_the_oracle_passes_only_the_expected_state() -> None:
    from agent.suite.scenarios import SCENARIOS, build, judge, snapshot
    s = Session()
    cases = build(s)
    assert len(cases) == 18 and {c.scenario for c in cases} == set(SCENARIOS)
    assert all(len({c.variant for c in cases if c.scenario == sc}) == 3 for sc in SCENARIOS)
    pristine = snapshot(s)
    assert not any(judge(c, Session(), pristine)[0] for c in cases)    # nothing done: fail
    # cancel_refund: do exactly the expected writes -> pass; one extra write -> fail
    c = next(x for x in cases if x.scenario == "cancel_refund")
    t = Session()
    t.verify_customer(email=t.customers[c.expect["customer"]]["email"])
    t.cancel_booking(c.expect["booking"], True)
    t.issue_refund(c.expect["booking"], True)
    assert judge(c, t, pristine) == (True, "ok")
    other = next(code for code, b in t.bookings.items() if b["customer_id"] != t.verified
                 and b["status"] == "confirmed")
    t.bookings[other]["bags"] += 1
    assert judge(c, t, pristine)[0] is False
    tr = next(x for x in cases if x.scenario == "transfer")
    assert judge(tr, Session(), pristine, transferred=True) == (True, "ok")


def test_proof_panel_values_equal_the_stub_gateways_record() -> None:
    pytest.importorskip("starlette")
    from pathlib import Path

    from agent.suite.run import NullMcp, NullPoller
    from agent.tasks import get
    from console.engine import Conversation, proof
    from console.llm_stream import ScriptedStreamingLLM
    from tests.test_console import FakeStack

    sess = {"calls": [{"i": 0, "tool_class": "flight_search", "server": "search",
                       "t_start_ms": 5200.0, "t_end_ms": 5240.0, "cold": False,
                       "predicted": True, "added_ms": 31.5, "held_ms": 0.0}],
            "decisions": [{"t_ms": 900.0, "outcome": "ok", "after_call": -1,
                           "trigger": "turn_start",
                           "ranked": [{"tool_class": "flight_search", "p": 0.61},
                                      {"tool_class": "user_lookup", "p": 0.2},
                                      {"tool_class": "escalate", "p": 0.1},
                                      {"tool_class": "other", "p": 0.05}],
                           "decisions": [{"tool_class": "flight_search", "warm": True,
                                          "copies": 1, "start_in_ms": 300, "prob": 0.61}]}]}

    class GwStack(FakeStack):              # serves the stub gateway's session record
        async def session(self, sid):
            return sess

    class Info:
        ok, cold, added_ms, held_ms, predicted, wall_ms = True, False, 31.5, 0.0, True, 40.0
        error = warm_source = hold_late_ms = None

    class Mcp(NullMcp):
        def call_tool(self, name, args):
            return {"structuredContent": {}}, Info()

    async def publish(ev):
        pass
    poller = NullPoller()
    poller.warm_since = {}
    conv = Conversation(get("skyair"), ScriptedStreamingLLM(
        [[("search_flights", {"origin": "BLR", "destination": "GOI"})]]),
        {"base": GwStack("base"), "pred": GwStack("pred")}, poller, publish,
        {"mirror": "concurrent", "hint_mode": "off", "baseline_policy": "scale_to_zero"},
        mcp_factory=lambda u: Mcp())
    asyncio.run(conv.user_turn("flights please"))
    p = conv.calls[0]["proof"]
    c, d = sess["calls"][0], sess["decisions"][0]
    assert p == proof(sess, 0, "flight_search", conv.calls[0]["server"], conv._server, None)
    assert (p["arrival_ms"], p["added_ms"], p["held_ms"], p["cold"], p["predicted"]) == (
        c["t_start_ms"], c["added_ms"], c["held_ms"], c["cold"], c["predicted"])
    assert p["decision"]["t_ms"] == d["t_ms"] and p["decision"]["top3"] == d["ranked"][:3]
    assert p["decision"]["copies"] == 1 and p["decision"]["start_in_ms"] == 300
    assert p["warm_requested_ms"] == 1200.0 and p["lead_ms"] == 4000.0
    assert p["verdict"] == "warm"
    sess["calls"][0]["cold"] = True
    assert proof(sess, 0, "flight_search", "search")["verdict"] == "cold_despite_prediction"
    # the page shows these fields as they are (no client-side computation of values)
    js = (Path(__file__).resolve().parent.parent / "console" / "static" / "app.js").read_text()
    for f in ("p.arrival_ms", "p.added_ms", "p.held_ms", "p.lead_ms", "d.t_ms", "d.copies",
              "d.start_in_ms", "p.pod_ready_before_ms"):
        assert f in js


def test_autopilot_accepts_suite_cases() -> None:
    pytest.importorskip("starlette")
    from starlette.testclient import TestClient

    from console.app import Console, make_app
    from tests.test_console import FakeGW, FakeStack, factory
    con = Console(llm=None, base=FakeStack("base"), pred=FakeStack("pred"), start_poller=False,
                  mcp_factory=factory({"base": FakeGW("base"), "pred": FakeGW("pred")}))
    played = []

    async def autopilot(turns=None, script=None):
        played.append(script)
    con.autopilot = autopilot
    with TestClient(make_app(con)) as tc:
        assert tc.post("/api/autopilot", json={"scenario": "suite:nope"}).status_code == 400
        r = tc.post("/api/autopilot", json={"scenario": "suite:transfer__typos"})
        assert r.status_code == 202
    assert played and "compensaton" in played[0][0]
