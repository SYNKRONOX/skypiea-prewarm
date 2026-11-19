"""Issue #22: a "% off your fare" promo discounts the fare only, as at book time."""

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


# ---------- #22 ----------
def test_promo_discounts_the_fare_not_baggage():
    s, b = _verified_with(lambda b, s: s._promo_ok("SKYWINTER10", b) and not b.get("promo")
                          and b["bags"] == 0)
    code = b["booking_code"]
    fare = s.fare_of(b)
    assert s.add_baggage(booking_code=code, bags=3, customer_confirmed=True)["added"] == 3
    r = s.apply_promo(booking_code=code, promo_code="SKYWINTER10", customer_confirmed=True)
    assert r["applied"] and r["discount"] == round(fare * 10 / 100)


