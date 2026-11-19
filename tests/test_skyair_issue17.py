"""Issue #17: book_flight checks and decrements seats_left by passengers; cancel returns them."""

from __future__ import annotations

from agent.skyair import world as W
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


# ---------- #17 ----------
def _flight_with(s, seats):
    return next(f for f in s.flights.values() if f["status"] == "scheduled"
                and f["date"] >= TODAY.isoformat() and f["seats_left"] == seats)


def test_booking_more_passengers_than_seats_is_refused():
    s = Session()
    f = _flight_with(s, 3)
    r = s.book_flight(f["flight_id"], "Saver", name="Nova Quill", email="nova@example.org",
                      passengers=4, customer_confirmed=True)
    assert r["booked"] is False and r["error"] == "not_enough_seats"
    assert s.flights[f["flight_id"]]["seats_left"] == 3


def test_booking_decrements_seats_and_cancel_returns_them():
    s = Session()
    f = _flight_with(s, 3)
    fid = f["flight_id"]
    q = s.book_flight(fid, "Saver", name="Nova Quill", email="nova@example.org", passengers=2)
    assert q["price"] == s.fare_prices(fid, 2)["Saver"] == 2 * s.fare_prices(fid, 1)["Saver"]
    r = s.book_flight(fid, "Saver", name="Nova Quill", email="nova@example.org",
                      passengers=2, customer_confirmed=True)
    assert r["booked"] and s.flights[fid]["seats_left"] == 1
    assert Session().flights[fid]["seats_left"] == 3           # other conversations untouched
    assert s.cancel_booking(booking_code=r["booking_code"], customer_confirmed=True)["cancelled"]
    assert s.flights[fid]["seats_left"] == 3


def test_full_flight_is_no_longer_offered():
    s = Session()
    f = _flight_with(s, 3)
    fid = f["flight_id"]
    assert s.book_flight(fid, "Saver", name="Nova Quill", email="nova@example.org",
                         passengers=3, customer_confirmed=True)["booked"]
    assert s.flights[fid]["seats_left"] == 0
    res = s.search_flights(f["origin"], f["destination"], f["date"])
    assert fid not in [x["flight_id"] for x in res.get("flights", [])]
    assert W.FARES  # world unchanged
