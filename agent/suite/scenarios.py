"""Skye scenario suite: 6 scenarios x 3 messy variants, judged ONLY by the final state of the
conversation's mock database (no LLM judge, nothing Skye says is read).

Customer turns are SCRIPTED per variant (fixed lines, sent in order whatever Skye replies) and
labelled so in every record. The people, codes, routes and dates are picked from the world's
own data by fixed rules (first match in id order), so the suite runs on any Skypiea Air world
that keeps the Session interface (bookings, customers, flights, new_customers, new_bookings,
transferred). Before W1 night the world was six fictional airports (codes SKA..SKF); after it,
an India-centred synthetic world. The two worlds have different people and places, so a
scenario's lines differ between "before" and "after"; its shape, its variants and its oracle
rule do not.

A run PASSES only if the database diff from the pristine world equals the expected diff:
exactly the expected bookings changed or created, with the expected values, and nothing else
(or, for the out-of-scope scenario, a transfer to a human and no write at all).
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

SCENARIOS = ["new_booking", "promo_booking", "change_date", "add_bag", "cancel_refund",
             "transfer"]
TITLES = {"new_booking": "New customer books a trip",
          "promo_booking": "Existing customer books with a promo",
          "change_date": "Change the date of a flight",
          "add_bag": "Add checked baggage",
          "cancel_refund": "Cancel with refund policy",
          "transfer": "Out-of-scope request ends in a transfer to a human"}
NEW_NAME, NEW_EMAIL = "Kavya Raman", "kavya.raman@example.net"


@dataclass
class Case:
    scenario: str
    variant: str              # typos | wrong_email | changes_mind | unsupported
    turns: list[str]          # scripted customer lines
    expect: dict[str, Any]    # what the oracle checks (also saved with the run)
    oracle: Callable[[Any, dict], tuple[bool, str]] = field(repr=False, default=None)

    @property
    def key(self) -> str:
        return f"{self.scenario}__{self.variant}"


# ------------------------------------------------------------------ world helpers
def place(s: Any, code: str, alt: int = 0) -> str:
    """How a customer names an airport: the city (new world, sometimes an alias), else the
    code (old world)."""
    f = getattr(s, "place_name", None)
    return f(code, alt) if f else code


def say_date(s: Any, iso: str) -> str:
    f = getattr(s, "say_date", None)
    return f(iso) if f else iso


def bookable(s: Any, f: dict) -> bool:
    today = getattr(s, "today", None)
    return f["status"] == "scheduled" and f["seats_left"] > 0 and (
        today is None or f["date"] >= today.isoformat())


def flights_on(s: Any, o: str, d: str, date: str) -> list[dict]:
    return sorted((f for f in s.flights.values() if f["origin"] == o and f["destination"] == d
                   and f["date"] == date and bookable(s, f)), key=lambda f: f["depart"])


def unique_customers(s: Any) -> list[dict]:
    names = [c["name"].lower() for c in s.customers.values()]
    return [c for c in sorted(s.customers.values(), key=lambda c: c["customer_id"])
            if names.count(c["name"].lower()) == 1]


def active(s: Any, cid: str) -> list[dict]:
    return [b for b in s.bookings.values() if b["customer_id"] == cid
            and b["status"] == "confirmed"]


def routes_with_two_dates(s: Any) -> list[tuple[str, str, str, str]]:
    """(origin, destination, date1, date2) with bookable flights on both dates, date2 > date1."""
    by: dict[tuple[str, str], set[str]] = {}
    for f in s.flights.values():
        if bookable(s, f):
            by.setdefault((f["origin"], f["destination"]), set()).add(f["date"])
    out = []
    for (o, d), ds in sorted(by.items()):
        ds_sorted = sorted(ds)
        if len(ds_sorted) >= 3:
            out.append((o, d, ds_sorted[1], ds_sorted[2]))
    return out


def wrong_email(email: str) -> str:
    local, _, dom = email.partition("@")
    return f"{local}{'s' if not local.endswith('s') else 'z'}@{dom}"


def promo_for(s: Any, flight: dict, fare: str) -> str | None:
    f = getattr(s, "promo_for", None)
    if f:
        return f(flight["flight_no"], fare)
    # old world (agent/skyair before W1 night): SKYWINTER10 = December 2026, paid >= $100
    return "SKYWINTER10" if flight["date"].startswith("2026-12") and \
        flight["price"] >= 100 and fare == "Saver" else None


def diff(pristine: dict[str, dict], now: dict[str, dict]) -> dict[str, dict]:
    """Booking code -> {"new": bool, field: (old, new)} for every booking that changed."""
    out: dict[str, dict] = {}
    for code, b in now.items():
        a = pristine.get(code)
        if a is None:
            out[code] = {"new": True, **{k: (None, v) for k, v in b.items()}}
            continue
        ch = {k: (a.get(k), b.get(k)) for k in set(a) | set(b)
              if a.get(k) != b.get(k) and k != "seats"}
        if ch:
            out[code] = {"new": False, **ch}
    return out


def _only(d: dict[str, dict], codes: set[str]) -> str | None:
    extra = sorted(set(d) - codes)
    return f"unexpected changes to {extra}" if extra else None


# ------------------------------------------------------------------ scenarios
def build(s: Any) -> list[Case]:
    """All 18 cases for the world of session `s` (a fresh Session)."""
    cust = unique_customers(s)
    used: set[str] = set()

    def pick(pred) -> dict:
        for c in cust:
            if c["customer_id"] not in used and pred(c):
                used.add(c["customer_id"])
                return c
        raise LookupError("no customer fits a suite rule")

    cases: list[Case] = []
    r2 = routes_with_two_dates(s)

    # 1. new customer books a trip --------------------------------------------------
    o, d, d1, d2 = r2[0]
    O, D = place(s, o), place(s, d)

    def new_booking_oracle(date: str, email: str):
        def check(sess, dd) -> tuple[bool, str]:
            new = [c for c, x in dd.items() if x["new"]]
            if len(new) != 1:
                return False, f"{len(new)} new bookings (expected 1)"
            b = sess.bookings[new[0]]
            if b["customer_id"] not in sess.new_customers:
                return False, "booked for an existing customer"
            if sess.customers[b["customer_id"]]["email"] != email:
                return False, f"wrong email {sess.customers[b['customer_id']]['email']!r}"
            f = sess.flights[b["flights"][0]]
            if (f["origin"], f["destination"], f["date"]) != (o, d, date):
                return False, f"wrong flight {f['origin']}-{f['destination']} {f['date']}"
            if b["fare"] != "Saver":
                return False, f"fare {b['fare']} (expected Saver)"
            return (False, x) if (x := _only(dd, set(new))) else (True, "ok")
        return check
    ex = {"origin": o, "destination": d, "fare": "Saver", "email": NEW_EMAIL}
    cases += [
        Case("new_booking", "typos", [
            f"hii, first time flying with u guys. im {NEW_NAME}",
            f"i want to fly frm {place(s, o, 1).lower()} to {D.lower()} on {say_date(s, d1)}, "
            "cheapest fare pls",
            f"the erliest flight is fine, saver fare. my emial is {NEW_EMAIL}",
            "yes i confirm, pls book it",
            "thx thats all"], {**ex, "date": d1}, new_booking_oracle(d1, NEW_EMAIL)),
        Case("new_booking", "wrong_email", [
            f"Hello! I've never flown with you before. My name is {NEW_NAME}.",
            f"I'd like to fly from {O} to {D} on {say_date(s, d1)}, the earliest flight, "
            "Saver fare.",
            f"My email is {NEW_EMAIL.replace('@', '')}",
            f"Sorry, typo. My email is {NEW_EMAIL}",
            "Yes, I confirm the booking.",
            "Thanks, that's all."], {**ex, "date": d1}, new_booking_oracle(d1, NEW_EMAIL)),
        Case("new_booking", "changes_mind", [
            f"Hi, I'm {NEW_NAME} and I'm new to Skypiea Air.",
            f"I'd like to fly from {O} to {D} on {say_date(s, d1)}.",
            f"Actually, make that {say_date(s, d2)} instead, same route. Earliest flight, "
            f"Saver fare. My email is {NEW_EMAIL}.",
            "Yes, I confirm the booking.",
            "Thanks, that's all."], {**ex, "date": d2}, new_booking_oracle(d2, NEW_EMAIL)),
    ]

    # 2. existing customer books with a promo ------------------------------------------
    pf = next((f, p) for f in sorted(s.flights.values(), key=lambda x: (x["date"], x["depart"],
                                                                        x["flight_no"]))
              if bookable(s, f) and (p := promo_for(s, f, "Saver"))
              and (f["origin"], f["destination"]) != (o, d))
    fl, promo = pf
    c2 = pick(lambda c: True)
    O2, D2 = place(s, fl["origin"]), place(s, fl["destination"])

    def promo_oracle(sess, dd) -> tuple[bool, str]:
        new = [c for c, x in dd.items() if x["new"]]
        if len(new) != 1:
            return False, f"{len(new)} new bookings (expected 1)"
        b = sess.bookings[new[0]]
        if b["customer_id"] != c2["customer_id"]:
            return False, "booked for another customer"
        f = sess.flights[b["flights"][0]]
        if (f["origin"], f["destination"], f["date"]) != (fl["origin"], fl["destination"],
                                                          fl["date"]):
            return False, f"wrong flight {f['origin']}-{f['destination']} {f['date']}"
        if b["fare"] != "Saver":
            return False, f"fare {b['fare']} (expected Saver)"
        if b.get("promo") != promo:
            return False, f"promo {b.get('promo')!r} (expected {promo})"
        return (False, x) if (x := _only(dd, set(new))) else (True, "ok")
    ex = {"customer": c2["customer_id"], "origin": fl["origin"],
          "destination": fl["destination"], "date": fl["date"], "fare": "Saver",
          "promo": promo}
    when = say_date(s, fl["date"])
    cases += [
        Case("promo_booking", "typos", [
            f"hi its {c2['name'].lower()}, emial {c2['email']}",
            f"i need a flght from {O2.lower()} to {D2.lower()} on {when}, saver",
            f"the {fl['depart']} one. is there any promo code i can use? {promo} maybe?",
            f"ok book it with {promo}, yes i confirm",
            "yes confirm the promo too",
            "thx bye"], ex, promo_oracle),
        Case("promo_booking", "wrong_email", [
            f"Hi, I'm {c2['name']}, my email is {wrong_email(c2['email'])}.",
            f"Sorry, wrong email. It's {c2['email']}.",
            f"I'd like to book {O2} to {D2} on {when}, the {fl['depart']} flight, Saver fare.",
            "Yes, I confirm the booking.",
            f"Can you apply the promo code {promo} to it? Yes, I confirm.",
            "Thanks, that's all."], ex, promo_oracle),
        Case("promo_booking", "changes_mind", [
            f"Hello, {c2['name']} here, email {c2['email']}.",
            f"I want to fly {O2} to {D2} on {when}, the {fl['depart']} flight, Flex fare.",
            "Hmm, actually Saver is fine, not Flex.",
            "Yes, I confirm the Saver booking.",
            f"Please apply promo {promo} to that booking. Yes, I confirm.",
            "Thanks, that's all."], ex, promo_oracle),
    ]

    # 3. change the date of a flight ---------------------------------------------------
    def change_ok(c: dict) -> bool:
        a = active(s, c["customer_id"])
        if len(a) != 1 or len(a[0]["flights"]) != 1:
            return False
        f = s.flights[a[0]["flights"][0]]
        return f["status"] == "scheduled" and len({
            x["date"] for x in s.flights.values() if bookable(s, x) and x["date"] > f["date"]
            and (x["origin"], x["destination"]) == (f["origin"], f["destination"])}) >= 2
    c3 = pick(change_ok)
    b3 = active(s, c3["customer_id"])[0]
    f3 = s.flights[b3["flights"][0]]
    later = sorted({x["date"] for x in s.flights.values() if bookable(s, x)
                    and (x["origin"], x["destination"]) == (f3["origin"], f3["destination"])
                    and x["date"] > f3["date"]})
    t1, t2 = later[0], later[1]

    def change_oracle(target: str):
        def check(sess, dd) -> tuple[bool, str]:
            b = sess.bookings[b3["booking_code"]]
            if b["status"] != "confirmed":
                return False, f"booking {b['status']}"
            fs = [sess.flights[x] for x in b["flights"]]
            if len(fs) != 1 or (fs[0]["origin"], fs[0]["destination"], fs[0]["date"]) != (
                    f3["origin"], f3["destination"], target):
                return False, "flight not moved to " + target + (
                    f" (now {fs[0]['date']})" if fs else "")
            return (False, x) if (x := _only(dd, {b3["booking_code"]})) else (True, "ok")
        return check
    O3, D3 = place(s, f3["origin"]), place(s, f3["destination"])
    ex = {"customer": c3["customer_id"], "booking": b3["booking_code"],
          "from_date": f3["date"]}
    cases += [
        Case("change_date", "typos", [
            f"hey, {c3['name'].lower()} here, emial {c3['email']}",
            f"i need to chnage my flight {O3.lower()} to {D3.lower()} to {say_date(s, t1)} "
            "pls, any time that day",
            "the first one that day is fine. yes i confirm the chnage and the fee",
            "yes, confirm",
            "thx"], {**ex, "to_date": t1}, change_oracle(t1)),
        Case("change_date", "wrong_email", [
            f"Hi, I'm {c3['name']}. My email is {wrong_email(c3['email'])}.",
            f"Oh sorry, it's {c3['email']}.",
            f"Please move my {O3} to {D3} flight to {say_date(s, t1)}, the first flight that "
            "day.",
            "Yes, I confirm the change and the fee.",
            "Thanks, that's all."], {**ex, "to_date": t1}, change_oracle(t1)),
        Case("change_date", "changes_mind", [
            f"Hello, this is {c3['name']}, email {c3['email']}.",
            f"I'd like to move my {O3} to {D3} flight to {say_date(s, t1)}.",
            f"Wait, sorry, make it {say_date(s, t2)} instead, the first flight that day.",
            "Yes, I confirm the change and the fee.",
            "Thanks, that's all."], {**ex, "to_date": t2}, change_oracle(t2)),
    ]

    # 4. add checked baggage -----------------------------------------------------------
    c4 = pick(lambda c: len(active(s, c["customer_id"])) == 1
              and active(s, c["customer_id"])[0]["bags"] <= 1)
    b4 = active(s, c4["customer_id"])[0]

    def bag_oracle(n: int):
        def check(sess, dd) -> tuple[bool, str]:
            b = sess.bookings[b4["booking_code"]]
            if b["bags"] != b4["bags"] + n:
                return False, f"bags {b['bags']} (expected {b4['bags'] + n})"
            if b["status"] != "confirmed" or b["flights"] != b4["flights"]:
                return False, "booking changed otherwise"
            return (False, x) if (x := _only(dd, {b4["booking_code"]})) else (True, "ok")
        return check
    ex = {"customer": c4["customer_id"], "booking": b4["booking_code"],
          "bags_before": b4["bags"]}
    cases += [
        Case("add_bag", "typos", [
            f"hi im {c4['name'].lower()}, my emial is {c4['email']}",
            "can u add one checked bag to my bookng",
            "yes i confrim, add 1 bag",
            "thx thats all"], {**ex, "add": 1}, bag_oracle(1)),
        Case("add_bag", "unsupported", [
            f"Hi, I'm {c4['name']}, email {c4['email']}.",
            "Can you upgrade me to first class and add one checked bag?",
            "OK, no upgrade then. Just add the one checked bag, please. Yes, I confirm.",
            "Yes, I confirm the bag.",
            "Thanks, that's all."], {**ex, "add": 1}, bag_oracle(1)),
        Case("add_bag", "changes_mind", [
            f"Hello, {c4['name']} here, email {c4['email']}.",
            "I'd like to add two checked bags to my booking.",
            "Actually, just one bag is enough. Yes, I confirm one bag.",
            "Yes, I confirm.",
            "Thanks, that's all."], {**ex, "add": 1}, bag_oracle(1)),
    ]

    # 5. cancel with refund policy -----------------------------------------------------
    def refundable(c: dict) -> bool:
        a = active(s, c["customer_id"])
        return len(a) == 1 and a[0]["fare"] in ("Flex", "Business")
    c5 = pick(refundable)
    b5 = active(s, c5["customer_id"])[0]
    code5 = b5["booking_code"]
    bad5 = code5[:-1] + ("X" if code5[-1] != "X" else "Y")

    def refund_oracle(sess, dd) -> tuple[bool, str]:
        b = sess.bookings[code5]
        if b["status"] != "cancelled":
            return False, f"booking {b['status']} (expected cancelled)"
        if not b.get("refunded"):
            return False, "cancelled but not refunded"
        return (False, x) if (x := _only(dd, {code5})) else (True, "ok")
    ex = {"customer": c5["customer_id"], "booking": code5, "fare": b5["fare"]}
    cases += [
        Case("cancel_refund", "typos", [
            f"hi, i need to cancle a trip. im {c5['name'].lower()}, emial {c5['email']}",
            f"pls cancel bookng {code5}, yes i confirm the cancelation",
            "and refund it to my card pls, yes i confirm the refund",
            "yes",
            "thx"], ex, refund_oracle),
        Case("cancel_refund", "wrong_email", [
            f"Hi, I want to cancel my trip. I'm {c5['name']}, email "
            f"{wrong_email(c5['email'])}.",
            f"Sorry, my email is {c5['email']}. The booking is {bad5}.",
            f"Oops, the booking code is {code5}. Please cancel it. Yes, I confirm the "
            "cancellation.",
            "And please refund it to my card. Yes, I confirm the refund.",
            "Thanks, that's all."], ex, refund_oracle),
        Case("cancel_refund", "unsupported", [
            f"Hello, I'm {c5['name']}, email {c5['email']}. I need to cancel booking {code5}.",
            "Can you refund it in cash to my friend's bank account instead of my card?",
            "OK, the original payment method is fine. Please cancel it, yes I confirm.",
            "And the refund, yes I confirm.",
            "Thanks, that's all."], ex, refund_oracle),
    ]

    # 6. out of scope -> transfer to a human -------------------------------------------
    c6 = pick(lambda c: True)

    def transfer_oracle(sess, dd) -> tuple[bool, str]:
        if not getattr(sess, "transferred", False):
            return False, "no transfer to a human"
        return (False, f"database changed: {sorted(dd)}") if dd else (True, "ok")
    ex = {"transfer": True, "writes": 0}
    cases += [
        Case("transfer", "typos", [
            "my bag was damgd on my last flght and i want compensaton",
            f"im {c6['name'].lower()}, {c6['email']}. i want to tlk to a real persn about "
            "the damage pls",
            "yes pls transfer me",
            "thx"], ex, transfer_oracle),
        Case("transfer", "unsupported", [
            "Hi, do I need a visa for Thailand?",
            "OK. Then I have a complaint: a staff member was rude to me at the gate. I want "
            "to speak to a human about it.",
            "Yes, please transfer me to a person.",
            "Thanks."], ex, transfer_oracle),
        Case("transfer", "changes_mind", [
            "Hi, can you book me a hotel near the airport?",
            "Never mind the hotel. I lost my laptop on the plane yesterday and need someone "
            "to help me find it. Please put me through to a human.",
            "Yes, transfer me please.",
            "Thanks."], ex, transfer_oracle),
    ]
    return cases


def snapshot(s: Any) -> dict[str, dict]:
    return copy.deepcopy(s.bookings)


def judge(case: Case, sess: Any, pristine: dict[str, dict],
          transferred: bool = False) -> tuple[bool, str]:
    """`transferred`: a transfer_to_human call succeeded (from the call log; the pre-W1-night
    Session did not keep it)."""
    if transferred and not getattr(sess, "transferred", False):
        sess.transferred = True
    return case.oracle(sess, diff(pristine, sess.bookings))
