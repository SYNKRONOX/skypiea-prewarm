"""Issue #10: a confirmed write runs only if it targets what was quoted (same booking code,
promo, bags, flight); a write without a booking code uses the quoted one."""

from __future__ import annotations

from console import guards

Q = {"tool": "cancel_booking", "args": {"booking_code": "5TGTBP"}, "summary": "..."}


def test_yes_to_one_booking_does_not_cancel_another():
    assert not guards.confirmed_ok("cancel_booking", "yes", Q, {"booking_code": "8RK2RN"})
    assert guards.confirmed_ok("cancel_booking", "yes", Q, {"booking_code": "5tgtbp"})
    assert guards.confirmed_ok("cancel_booking", "yes", Q, {})          # code filled from quote


def test_other_quoted_fields_must_match_too():
    q = {"tool": "add_baggage", "args": {"booking_code": "5TGTBP", "bags": 1}}
    assert not guards.confirmed_ok("add_baggage", "yes", q, {"booking_code": "5TGTBP",
                                                             "bags": 3})
    q = {"tool": "change_flight", "args": {"booking_code": "5TGTBP",
                                           "new_flight_no": "SKY137-20JAN"}}
    assert guards.confirmed_ok("change_flight", "yes", q, {"new_flight_no": "SKY137-20JAN"})
    assert not guards.confirmed_ok("change_flight", "yes", q, {"new_flight_no": "SKY140-21JAN"})


def test_mismatch_does_not_fall_back_to_the_keyword_path():
    assert not guards.confirmed_ok("cancel_booking", "yes, cancel it", Q,
                                   {"booking_code": "8RK2RN"})


def test_engine_fills_the_quoted_target():
    args = {"customer_confirmed": True}
    guards.fill_from_quote("cancel_booking", args, Q)
    assert args["booking_code"] == "5TGTBP"
    other = {"booking_code": "8RK2RN"}
    guards.fill_from_quote("cancel_booking", other, Q)
    assert other["booking_code"] == "8RK2RN"                            # never overwritten
