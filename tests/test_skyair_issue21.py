"""Issue #21: change_flight to the flight the booking is already on is refused, no fee."""

from __future__ import annotations

from agent.skyair.tools import ACTIVE, TODAY, Session


def _verified_with(pred):
    s = Session()
    for b in s.bookings.values():
        if b["status"] == ACTIVE and pred(b, s):
            cust = s.customers[b["customer_id"]]
            s.verify_customer(email=cust["email"])
            if s.verified == b["customer_id"]:
                return s, b
    raise AssertionError("no booking found")


def _one_flight_saver(b, s):
    return (len(b["flights"]) == 1 and b["fare"] == "Saver"
            and s.flights[b["flights"][0]]["status"] == "scheduled"
            and s.flights[b["flights"][0]]["date"] >= TODAY.isoformat())


# ---------- #21 ----------
def test_change_to_the_same_flight_is_refused_and_free():
    s, b = _verified_with(_one_flight_saver)
    paid = b["paid"]
    for confirmed in (False, True):
        r = s.change_flight(booking_code=b["booking_code"], new_flight_no=b["flights"][0],
                            customer_confirmed=confirmed)
        assert r.get("changed") is False and r["error"] == "same_flight", r
    assert s.bookings[b["booking_code"]]["paid"] == paid


