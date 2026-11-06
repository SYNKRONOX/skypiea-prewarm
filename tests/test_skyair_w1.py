"""Skypiea Air W1: default booking (P0 #5), valid codes in errors and the customer sidebar
(#6), new customers book a trip (#7), offers (#8), price truth check (#10), booking state
for the boarding-pass card (#11). Fictional data only."""

from __future__ import annotations

import asyncio
import json

import pytest

from agent.skyair.tools import OFFERS, Session, pristine, seat_for
from console import truth


def _customers_by_active() -> tuple[dict, dict]:
    db = pristine()
    names = [c["name"] for c in db["customers"]]
    one = many = None
    for c in db["customers"]:
        act = [b for b in db["bookings"] if b["customer_id"] == c["customer_id"]
               and b["status"] == "confirmed"]
        if names.count(c["name"]) != 1:
            continue
        if len(act) == 1 and one is None:
            one = {**c, "codes": [b["booking_code"] for b in act]}
        if len(act) > 1 and many is None:
            many = {**c, "codes": sorted(b["booking_code"] for b in act)}
    assert one and many
    return one, many


def _scheduled_flight(month: str = "2026-12") -> str:
    return next(f["flight_no"] for f in pristine()["flights"] if f["status"] == "scheduled"
                and f["seats_left"] > 0 and f["date"].startswith(month))


# ---------------------------------------------------------------- P0 #5
def test_one_active_booking_is_used_when_the_code_is_missing() -> None:
    one, _ = _customers_by_active()
    s = Session()
    assert s.get_booking()["error"] == "not_verified"            # verification unchanged
    assert "your_bookings" not in s.get_booking()
    assert "your_bookings" not in s.get_booking("ZZZZZZ")        # nothing leaks before it
    assert s.verify_customer(email=one["email"])["verified"]
    assert s.get_booking()["booking_code"] == one["codes"][0]
    q = s.add_baggage(bags=1)
    assert q["confirmation_needed"] and q["price"] == 35
    assert s.add_baggage(bags=1, customer_confirmed=True)["added"] == 1


def test_several_active_bookings_ask_which_one_and_wrong_codes_list_valid_ones() -> None:
    _, many = _customers_by_active()
    s = Session()
    s.verify_customer(email=many["email"])
    r = s.cancel_booking(customer_confirmed=True)
    assert r["error"] == "which_booking" and r["your_bookings"] == many["codes"]
    assert all(s.bookings[c]["status"] == "confirmed" for c in many["codes"])   # nothing done
    r = s.change_flight(booking_code="<booking_code>", new_flight_no="SA999")
    assert r["error"] == "not_found" and set(many["codes"]) <= set(r["your_bookings"])
    other = next(b["booking_code"] for b in pristine()["bookings"]
                 if b["customer_id"] != many["customer_id"])
    r = s.get_booking(other)
    assert r["error"] == "not_allowed" and other not in r["your_bookings"]


# ---------------------------------------------------------------- P1 #6 sidebar
def test_sidebar_lists_codes_fare_and_fee_rules_only_after_verification() -> None:
    one, _ = _customers_by_active()
    s = Session()
    assert s.sidebar() is None
    s.verify_customer(email=one["email"])
    assert s.sidebar_due
    text = s.sidebar()
    b = s.bookings[one["codes"][0]]
    assert one["codes"][0] in text and b["fare"] in text and "change fee $" in text
    assert "Extra checked bag: $35" in text and one["email"] not in text


# ---------------------------------------------------------------- P1 #7 new customer
def test_new_customer_finds_books_and_manages_a_trip_and_reset_clears_it() -> None:
    s = Session()
    assert s.find_customer("Nova Quill")["found"] == 0
    assert "new customer" in s.find_customer("Nova Quill")["message"]
    fno = _scheduled_flight()
    f = s.flights[fno]
    res = s.search_flights(f["origin"], f["destination"], f["date"])
    row = next(x for x in res["flights"] if x["flight_no"] == fno)
    assert row["fares"] == {"Saver": f["price"], "Flex": f["price"] + 40,
                            "Business": 2 * f["price"] + 60}
    q = s.book_flight(fno, "Flex", name="Nova Quill", email="nova@example.org")
    assert q["confirmation_needed"] and q["price"] == f["price"] + 40 and not s.verified
    r = s.book_flight(fno, "Flex", name="Nova Quill", email="nova@example.org",
                      customer_confirmed=True)
    assert r["booked"] and r["new_customer"] and s.verified == r["customer_id"]
    code = r["booking_code"]
    again = s.book_flight(fno, "Flex", customer_confirmed=True)       # "yes, I confirm" twice
    assert again["error"] == "duplicate" and again["booking_code"] == code
    assert len(s.new_bookings) == 1
    assert code not in {b["booking_code"] for b in pristine()["bookings"]}
    assert r["flight"]["seat"] == seat_for(code, fno)
    assert s.get_booking()["booking_code"] == code                  # one active booking
    later = next((x["flight_no"] for x in s.search_flights(f["origin"], f["destination"])
                  ["flights"] if x["flight_no"] != fno), None)
    if later:
        ch = s.change_flight(new_flight_no=later, customer_confirmed=True)
        assert ch["changed"] and ch["from"]["flight_no"] == fno and ch["to"]["flight_no"] == later
    assert s.add_baggage(bags=1, customer_confirmed=True)["bags"] == 1
    assert s.list_offers()["offers"] == OFFERS
    st = truth.state(s)
    assert st["verified"]["new_customer"] and st["bookings"][0]["new"]
    assert [c["what"] for c in st["bookings"][0]["changes"]][0] == "booking"
    assert s.cancel_booking(customer_confirmed=True)["cancelled"]
    s.reset()
    assert code not in s.bookings and not s.new_customers and s.verified is None


def test_existing_email_cannot_book_without_verification() -> None:
    one, _ = _customers_by_active()
    s = Session()
    r = s.book_flight(_scheduled_flight(), name="Someone", email=one["email"],
                      customer_confirmed=True)
    assert r["error"] == "existing_customer" and not s.verified


# ---------------------------------------------------------------- P1 #8 offers
def test_promo_quote_apply_conditions_and_once_per_booking() -> None:
    s = Session()
    fno = _scheduled_flight()
    r = s.book_flight(fno, "Business", name="Nova Quill", email="nova@example.org",
                      customer_confirmed=True)
    paid = r["paid"]
    assert s.apply_promo(promo_code="BAG35")["applied"] is False        # no bag yet
    q = s.apply_promo(promo_code="biz25")
    assert q["confirmation_needed"] and q["discount"] == 25 and q["new_total"] == paid - 25
    a = s.apply_promo(promo_code="BIZ25", customer_confirmed=True)
    assert a["applied"] and a["paid"] == paid - 25
    assert s.apply_promo(promo_code="SKYWINTER10", customer_confirmed=True)["applied"] is False
    assert s.apply_promo(promo_code="FREE100")["error"] == "unknown_promo"
    assert truth.write_outcome("apply_promo", a) == "success"


# ---------------------------------------------------------------- P1 #10 prices
@pytest.mark.parametrize("text,want", [
    ("The change fee is $50.", [50.0]), ("Total: $1,014.00 or 99 USD", [1014.0, 99.0]),
    ("That costs 35 dollars.", [35.0]), ("No numbers here, flight SA101.", [])])
def test_amounts(text, want) -> None:
    assert truth.amounts(text) == want


def test_price_check_flags_amounts_no_tool_returned() -> None:
    r = truth.check_prices("The fee is $50 and the bag is $35.", {35.0})
    assert r["unsourced"] == [50.0] and r["badges"] == ["Not from a tool: $50"]
    assert truth.check_prices("A bag is $35.", {35.0})["verdict"] == "sourced"
    assert truth.check_prices("Happy to help.", set())["verdict"] == "none"


# ---------------------------------------------------------------- engine
def test_engine_sidebar_vocab_note_and_price_badge() -> None:
    pytest.importorskip("starlette")
    from agent.tasks import get
    from console.engine import TURN_FIELDS, Conversation, to_csv
    from console.llm_stream import ScriptedStreamingLLM
    from tests.test_console import FakeGW, FakeStack, NullPoller, factory

    one, _ = _customers_by_active()
    gws = {"base": FakeGW("base"), "pred": FakeGW("pred")}

    async def publish(ev):
        pass
    llm = ScriptedStreamingLLM(
        [[("verify_customer", {"email": one["email"]}), ("list_offers", {})], []],
        answers=["You're verified. The change fee on your fare is $75.", "Sorry."])
    conv = Conversation(get("skyair"), llm, {"base": FakeStack("base"),
                                             "pred": FakeStack("pred")}, NullPoller(),
                        publish, {"mirror": "concurrent", "hint_mode": "off",
                                  "baseline_policy": "scale_to_zero"}, mcp_factory=factory(gws))
    asyncio.run(conv.user_turn("hi"))
    sys_msgs = [m["content"] for m in conv.messages if m["role"] == "system"]
    # the sidebar is part of the top system message (no system message mid-conversation
    # except the truth note)
    top = conv.messages[0]["content"]
    assert "CUSTOMER SIDEBAR" in top and one["codes"][0] in top and top.startswith("You are Skye")
    assert sum("CUSTOMER SIDEBAR" in m for m in sys_msgs) == 1
    offers = next(c for c in conv.calls if c["tool"] == "list_offers")
    assert offers["tool_class"] == "promo_code_lookup" and offers.get("out_of_vocab")
    assert "no prediction, normal behaviour" in offers["note"]
    t = conv.truth[0]
    assert t["prices_unsourced"] == [75.0] and "Not from a tool: $75" in t["badges"]
    assert any("$75" in m and "no tool returned" in m for m in sys_msgs)
    asyncio.run(conv.user_turn("ok"))
    assert conv.turns[0]["llm_model"] == conv.llm.model
    csv = to_csv(conv.turns, TURN_FIELDS)
    assert "truth_prices_unsourced" in csv and "75" in csv
    assert one["email"] not in json.dumps(conv.calls)


def test_report_metrics_and_scenario_check() -> None:
    pytest.importorskip("starlette")
    from agent.tasks import get
    from agent.tasks.skye import SCENARIOS, scenario_done
    from console.bakeoff import report, table
    from console.engine import Conversation
    from console.llm_stream import ScriptedStreamingLLM
    from tests.test_console import FakeGW, FakeStack, NullPoller, factory

    gws = {"base": FakeGW("base"), "pred": FakeGW("pred")}

    async def publish(ev):
        pass
    llm = ScriptedStreamingLLM(
        [[("find_customer", {"name": "Nova Quill"}),
          ("change_flight", {"booking_code": "QQQQQQ", "new_flight_no": "SA1"}),
          ("book_flight", {"flight_no": _scheduled_flight(), "name": "Nova Quill",
                           "email": "nova@example.org", "customer_confirmed": True})]],
        answers=["Booked! Your total is $5."], model="m1")
    conv = Conversation(get("skyair"), llm, {"base": FakeStack("base"),
                                             "pred": FakeStack("pred")}, NullPoller(),
                        publish, {"mirror": "concurrent", "hint_mode": "off",
                                  "baseline_policy": "scale_to_zero"}, mcp_factory=factory(gws))
    asyncio.run(conv.user_turn(SCENARIOS["new_customer"][0]))
    r = report(conv, "new_customer")
    assert r["completed"] is True and r["invented_codes"] == 1
    assert r["first_try_valid"] == round(2 / 3, 3) and "not_verified" in r["errors"]
    assert r["invented_prices"] == 1 and r["llm_model"] == "m1" and r["llm_replies"] == 4
    assert scenario_done("failed_verification", conv.backend, conv.writes, conv.calls) is False
    assert "| m1 | 1 | 1 / 1 |" in table([{**r, "rep": 1}])
    assert "no calls" in table([{**r, "rep": 1, "first_try_valid": None}])   # out of scope


def test_a_wrong_code_the_customer_typed_is_not_counted_as_invented() -> None:
    pytest.importorskip("starlette")
    from agent.tasks import get
    from console.bakeoff import report
    from console.engine import Conversation
    from console.llm_stream import ScriptedStreamingLLM
    from tests.test_console import FakeGW, FakeStack, NullPoller, factory

    gws = {"base": FakeGW("base"), "pred": FakeGW("pred")}

    async def publish(ev):
        pass
    llm = ScriptedStreamingLLM([[("verify_customer", {"booking_code": "ZZZZZZ"})]])
    conv = Conversation(get("skyair"), llm, {"base": FakeStack("base"),
                                             "pred": FakeStack("pred")}, NullPoller(),
                        publish, {"mirror": "concurrent", "hint_mode": "off",
                                  "baseline_policy": "scale_to_zero"}, mcp_factory=factory(gws))
    asyncio.run(conv.user_turn("My booking code is ZZZZZZ."))
    r = report(conv, "failed_verification")
    assert r["invented_codes"] == 0 and r["unknown_codes_from_customer"] == 1


def test_find_customer_with_proof_verifies_by_the_same_rule() -> None:
    one, _ = _customers_by_active()
    s = Session()
    r = s.find_customer(one["name"], email=one["email"])
    assert r["found"] == 1 and r["verified"] is True and s.verified == one["customer_id"]
    s2 = Session()
    bad = s2.find_customer(one["name"], email="someone.else@example.com")
    assert bad["verified"] is False and s2.verified is None and "bookings" not in bad
    s3 = Session()                     # the code of ANOTHER customer does not verify this name
    other = next(b["booking_code"] for b in pristine()["bookings"]
                 if b["customer_id"] != one["customer_id"])
    assert s3.find_customer(one["name"], booking_code=other)["verified"] is False
    assert Session().find_customer("Nova Quill", email="n@example.org")["found"] == 0
