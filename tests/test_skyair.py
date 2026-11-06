"""Skypiea Air (fictional) mock database, tools, verification flow, and Skye in the console
engine with a scripted LLM. Offline."""

from __future__ import annotations

import asyncio
import json

import pytest

from agent.skyair.generate import OUT, build
from agent.skyair.tools import Session, personas, pristine


def test_database_is_deterministic_and_sized() -> None:
    assert json.loads(OUT.read_text()) == json.loads(json.dumps(build()))
    d = pristine()
    assert len(d["customers"]) == 50 and len(d["bookings"]) == 150
    assert "Skypiea Air" in d["airline"]
    names = [c["name"] for c in d["customers"]]
    assert any(names.count(n) > 1 for n in names)            # ambiguity on purpose
    flights = {f["flight_no"] for f in d["flights"]}
    assert all(f in flights for b in d["bookings"] for f in b["flights"])


def _persona():
    return next(p for p in personas() if "booking_code" in p)


def test_lookup_ambiguous_and_unknown() -> None:
    s = Session()
    amb = next(p for p in personas() if "AMBIGUOUS" in p.get("note", ""))
    r = s.find_customer(amb["name"])
    assert r["ambiguous"] and "customer_id" not in r and "@" not in json.dumps(r)
    assert s.find_customer("Nobody Here")["found"] == 0
    r = s.find_customer(_persona()["name"])
    assert r["found"] == 1 and r["verified"] is False


def test_verification_gates_everything_and_refuses_politely() -> None:
    p, s = _persona(), Session()
    assert s.get_booking(p["booking_code"])["error"] == "not_verified"
    bad = s.verify_customer(name=p["name"], email="wrong@example.com")
    assert bad["verified"] is False and "could not verify" in bad["message"]
    assert s.get_customer_details()["error"] == "not_verified"
    assert s.verify_customer(name=p["name"], booking_code=p["booking_code"])["verified"]
    assert p["booking_code"] in s.get_customer_details()["bookings"]
    other = next(b for b in pristine()["bookings"] if b["customer_id"] != s.verified)
    assert s.get_booking(other["booking_code"])["error"] == "not_allowed"


def test_change_needs_confirmation_persists_and_reset_restores() -> None:
    p, s = _persona(), Session()
    s.verify_customer(name=p["name"], email=p["email"])
    b = s.get_booking(p["booking_code"])
    f0 = b["flights"][0]
    alt = next(f for f in s.search_flights(f0["origin"], f0["destination"])["flights"]
               if f["flight_no"] != f0["flight_no"])
    q = s.change_flight(p["booking_code"], f0["flight_no"], alt["flight_no"])
    assert q["confirmation_needed"] and "change_fee" in q
    assert s.get_booking(p["booking_code"])["flights"][0]["flight_no"] == f0["flight_no"]
    done = s.change_flight(p["booking_code"], f0["flight_no"], alt["flight_no"], True)
    assert done["changed"]
    assert s.get_booking(p["booking_code"])["flights"][0]["flight_no"] == alt["flight_no"]
    assert s.add_baggage(p["booking_code"], 1, True)["added"] == 1
    s.reset()
    s.verify_customer(name=p["name"], email=p["email"])
    assert s.get_booking(p["booking_code"])["flights"][0]["flight_no"] == f0["flight_no"]


def test_refund_only_if_eligible_and_cancelled() -> None:
    s = Session()
    d = pristine()
    saver = next(b for b in d["bookings"] if b["fare"] == "Saver" and all(
        f["status"] == "scheduled" for f in d["flights"] if f["flight_no"] in b["flights"]))
    s.verified = saver["customer_id"]
    assert s.issue_refund(saver["booking_code"], True)["refunded"] is False
    flex = next(b for b in d["bookings"] if b["fare"] == "Flex")
    s.verified = flex["customer_id"]
    assert s.issue_refund(flex["booking_code"], True)["refunded"] is False   # not cancelled
    assert s.cancel_booking(flex["booking_code"], True)["cancelled"]
    assert s.issue_refund(flex["booking_code"])["confirmation_needed"]
    assert s.issue_refund(flex["booking_code"], True)["refunded"] is True


def test_unknown_or_private_tool_names_are_refused() -> None:
    s = Session()
    assert s.call("drop_tables", {})["error"] == "unknown_tool"
    assert s.call("reset", {})["error"] == "unknown_tool"
    assert s.call("_booking", {})["error"] == "unknown_tool"
    assert s.call("find_customer", {"nope": 1})["error"] == "bad_arguments"


def test_skye_in_the_console_engine() -> None:
    pytest.importorskip("starlette")
    from agent.tasks import get
    from agent.tasks.skye import CLASS
    from console.engine import Conversation
    from console.llm_stream import ScriptedStreamingLLM
    from tests.test_console import FakeGW, FakeStack, NullPoller, factory

    p = _persona()
    gws = {"base": FakeGW("base"), "pred": FakeGW("pred")}

    async def publish(ev):
        pass
    llm = ScriptedStreamingLLM([[("find_customer", {"name": p["name"]}),
                                 ("verify_customer", {"name": p["name"], "email": p["email"]}),
                                 ("get_booking", {"booking_code": p["booking_code"]}),
                                 ("invent_coupon", {"code": "X"})]])
    conv = Conversation(get("skyair"), llm, {"base": FakeStack("base"),
                                             "pred": FakeStack("pred")}, NullPoller(),
                        publish, {"mirror": "concurrent", "hint_mode": "off",
                                  "baseline_policy": "scale_to_zero"}, mcp_factory=factory(gws))
    asyncio.run(conv.user_turn("hi"))
    seen = conv.messages
    # gateways got the vocabulary classes and NO customer data
    assert [x[1:] for x in gws["pred"].seen] == [
        (CLASS["find_customer"], {}), (CLASS["verify_customer"], {}),
        (CLASS["get_booking"], {}), ("other", {})]
    tool_msgs = [m["content"] for m in seen if m.get("role") == "tool"]
    assert json.loads(tool_msgs[1])["verified"] is True
    assert json.loads(tool_msgs[2])["booking_code"] == p["booking_code"]
    assert "unknown tool" in tool_msgs[3]
    assert conv.calls[3]["note"] == "unknown tool: no prediction, normal behaviour"
    assert p["email"] not in json.dumps(conv.calls)


def test_verification_ignores_made_up_ids_but_not_contradictions() -> None:
    p, s = _persona(), Session()
    assert s.verify_customer(name=p["name"], customer_id="12345", email=p["email"])["verified"]
    s = Session()
    assert s.verify_customer(email=p["email"].upper(), booking_code=p["booking_code"].lower()
                             )["verified"]
    other = next(c for c in pristine()["customers"] if c["name"] != p["name"])
    assert not Session().verify_customer(name=other["name"], email=p["email"])["verified"]
    assert not Session().verify_customer(customer_id=other["customer_id"],
                                         email=p["email"])["verified"]
    wrong = next(b for b in pristine()["bookings"] if b["customer_id"] != other["customer_id"]
                 and b["customer_id"] != Session().verify_customer(email=p["email"])[
                     "customer_id"])
    assert not Session().verify_customer(email=p["email"],
                                         booking_code=wrong["booking_code"])["verified"]


def test_change_flight_errors_say_not_changed_and_offer_alternatives() -> None:
    p, s = _persona(), Session()
    s.verify_customer(email=p["email"])
    r = s.change_flight(p["booking_code"], "SA1", "SKA123", True)
    assert r["changed"] is False and "NOT made" in r["message"]
    assert "alternatives" in r or "booking_flights" in r


def test_skye_argument_synonyms() -> None:
    from agent.tasks.skye import SKYE

    a = SKYE.normalize_args("change_flight", {"new_flight_id": "SA1", "booking_id": "X",
                                              "junk": 1})
    assert a == {"new_flight_no": "SA1", "booking_code": "X", "junk": 1}
    # an explicit canonical name wins over a synonym
    assert SKYE.normalize_args("change_flight", {"new_flight_no": "A", "new_flight_id": "B"}
                               )["new_flight_no"] == "A"
