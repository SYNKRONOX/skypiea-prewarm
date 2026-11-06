"""Skypiea Air mock tools over the in-repo database (agent/skyair/skyair_db.json).

A ``Session`` holds one conversation's state: a private copy of the bookings (writes persist
within the session; ``reset()`` restores the database) and who has been verified. Tools never
invent data: unknown ids give an explicit "not found". Identity rules:
- find_customer(name): 0 matches -> not found; several -> "ambiguous: ask for email or booking
  code" (no personal data listed); 1 -> customer id, still unverified.
- verify_customer: the email or a booking code must belong to the customer; otherwise a polite
  refusal. Everything about a customer or booking needs that customer verified first.
- Changes (change_flight, cancel_booking, add_baggage, issue_refund) need
  ``customer_confirmed: true``; without it they return a quote and ask for confirmation.
- issue_refund only if eligible: refundable fare (Flex, Business) or a flight cancelled by
  the airline, and the booking is cancelled.
- A booking tool called WITHOUT a booking code uses the verified customer's booking if they
  have exactly one active (confirmed) booking; with several it returns their codes and asks
  which one. A wrong code returns the verified customer's valid codes (never before
  verification). Verification itself is unchanged.
- New customers (W1): a name that is not found can book a trip with book_flight (name +
  email not yet known to the airline + flight + fare). The new customer id and booking code
  are fictional, live only in this session (reset() clears them), and the new customer is
  verified by that booking. An email that belongs to an existing customer must be verified.
- Offers (W1, fictional): list_offers() and apply_promo(booking_code, promo_code).
- Seats are fictional and deterministic (from the booking code and flight number). Every
  successful write is also kept as a change (old -> new) for the booking card.
"""

from __future__ import annotations

import copy
import json
import random
import zlib
from functools import lru_cache
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).with_name("skyair_db.json")


@lru_cache(maxsize=1)
def pristine() -> dict:
    return json.loads(DB_PATH.read_text())


# Fare prices for NEW bookings (fictional rule): Saver = the flight's price, Flex = +$40,
# Business = 2 x price + $60, per passenger. Existing bookings keep their recorded price.
FARE_PRICE = {"Saver": (1, 0), "Flex": (1, 40), "Business": (2, 60)}
# Current offers (fictional). kind: pct = percent of the fare paid; credit = fixed dollars.
OFFERS = [
    {"promo_code": "SKYWINTER10", "title": "Winter sale: 10% off your fare",
     "kind": "pct", "value": 10, "conditions": "Bookings with all flights in December 2026; "
     "fare paid at least $100; one promo per booking."},
    {"promo_code": "BAG35", "title": "$35 back on a checked bag",
     "kind": "credit", "value": 35, "conditions": "Bookings with at least one checked bag; "
     "one promo per booking."},
    {"promo_code": "BIZ25", "title": "$25 off Business", "kind": "credit", "value": 25,
     "conditions": "Business fare bookings only; one promo per booking."},
]
ACTIVE = "confirmed"


def seat_for(code: str, flight_no: str) -> str:
    """A fictional, deterministic seat for a booking on a flight."""
    h = zlib.crc32(f"{code}:{flight_no}".encode())
    return f"{5 + h % 26}{'ABCDEF'[(h // 26) % 6]}"


class Session:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        db = pristine()
        self.db = db
        self.bookings = {b["booking_code"]: copy.deepcopy(b) for b in db["bookings"]}
        self.customers = {c["customer_id"]: c for c in db["customers"]}
        self.customers = dict(self.customers)
        self.flights = {f["flight_no"]: f for f in db["flights"]}
        self.verified: str | None = None
        self.new_customers: set[str] = set()      # created in this session (book_flight)
        self.new_bookings: set[str] = set()
        self.changes: list[dict] = []              # successful writes, old -> new
        self.sidebar_due = False                   # the engine refreshes Skye's sidebar

    # ---------------- helpers ----------------
    def _need(self, customer_id: str | None) -> dict | None:
        if not self.verified:
            return {"error": "not_verified", "message": "The customer is not verified yet. Ask "
                    "for their email address or a booking code and call verify_customer."}
        if customer_id and customer_id != self.verified:
            return {"error": "not_allowed", "message": "That customer is not the verified one."}
        return None

    def codes_of(self, cid: str | None, active_only: bool = False) -> list[str]:
        return sorted(code for code, b in self.bookings.items() if b["customer_id"] == cid
                      and (not active_only or b["status"] == ACTIVE))

    def _booking(self, code: str | None) -> tuple[dict | None, dict | None]:
        code = str(code or "").strip().upper()
        if not code:
            # no code given: the verified customer's only active booking, else ask
            err = self._need(None)
            if err:
                return None, err
            active = self.codes_of(self.verified, active_only=True)
            if len(active) == 1:
                return self.bookings[active[0]], None
            if not active:
                return None, {"error": "no_active_booking", "message": "This customer has "
                              "no active booking.", "your_bookings": self.codes_of(
                                  self.verified)}
            return None, {"error": "which_booking", "message": "The customer has several "
                          "active bookings: ask which one (nothing was done).",
                          "your_bookings": active}
        b = self.bookings.get(code)
        if b is None:
            err = self._need(None)
            if err:
                return None, {**err, "detail": f"No booking {code!r}."}
            return None, {"error": "not_found", "message": f"No booking {code!r}; nothing was "
                          "done. Use one of the customer's booking codes.",
                          "your_bookings": self.codes_of(self.verified)}
        err = self._need(b["customer_id"])
        if err and self.verified:
            err = {**err, "your_bookings": self.codes_of(self.verified)}
        return (None, err) if err else (b, None)

    def _flight_view(self, no: str, code: str | None = None) -> dict:
        f = self.flights[no]
        v = {k: f[k] for k in ("flight_no", "origin", "destination", "date", "depart",
                               "arrive", "status")}
        if code:
            b = self.bookings.get(code) or {}
            v["seat"] = (b.get("seats") or {}).get(no) or seat_for(code, no)
        return v

    def _changed(self, code: str, what: str, old: Any, new: Any) -> None:
        self.changes.append({"booking_code": code, "what": what, "old": old, "new": new})
        self.sidebar_due = True

    def _eligible(self, b: dict) -> bool:
        fare_ok = self.db["fare_rules"][b["fare"]]["refundable"]
        airline = any(self.flights[f]["status"] == "cancelled_by_airline" for f in b["flights"])
        return fare_ok or airline

    # ---------------- tools ----------------
    def find_customer(self, name: str = "") -> dict:
        hits = [c for c in self.customers.values() if c["name"].lower() == name.strip().lower()]
        if not hits:
            return {"found": 0, "message": f"No customer named {name!r}. Ask them to check "
                    "the spelling. If they are new to Skypiea Air, welcome them as a new "
                    "customer: ask origin, destination and travel date, call search_flights, "
                    "and offer to book with book_flight (their name and email)."}
        if len(hits) > 1:
            return {"found": len(hits), "ambiguous": True, "message": "Several customers have "
                    "this name. Ask for their email address or a booking code to verify."}
        return {"found": 1, "customer_id": hits[0]["customer_id"], "verified": False,
                "message": "Ask for the email address or a booking code to verify identity."}

    def verify_customer(self, name: str = "", customer_id: str = "", email: str = "",
                        booking_code: str = "") -> dict:
        """Identity proof = an email address or a booking code. The customer is found FROM the
        proof; a given name must match it, and a given customer_id must not name a DIFFERENT
        real customer (an id that does not exist at all, e.g. one the model made up, is
        ignored)."""
        refuse = {"verified": False, "message": "I could not verify this customer with those "
                  "details. Politely say so; do not share any account information."}
        found = None
        if email:
            found = next((c for c in self.customers.values()
                          if c["email"] == email.strip().lower()), None)
        if found is None and booking_code:
            b = self.bookings.get(booking_code.strip().upper())
            found = self.customers.get(b["customer_id"]) if b else None
        if found is None:
            return refuse
        if name and found["name"].lower() != name.strip().lower():
            return refuse
        if customer_id and customer_id in self.customers and customer_id != found["customer_id"]:
            return refuse
        if email and booking_code:      # both given: both must belong to this customer
            b = self.bookings.get(booking_code.strip().upper())
            if email.strip().lower() != found["email"] or not b or \
                    b["customer_id"] != found["customer_id"]:
                return refuse
        self.verified = found["customer_id"]
        self.sidebar_due = True
        return {"verified": True, "customer_id": found["customer_id"], "name": found["name"],
                "bookings": self.codes_of(found["customer_id"])}

    def get_customer_details(self, customer_id: str = "") -> dict:
        err = self._need(customer_id or self.verified)
        if err:
            return err
        c = self.customers[self.verified]
        return {**c, "bookings": self.codes_of(c["customer_id"])}

    def get_booking(self, booking_code: str = "") -> dict:
        b, err = self._booking(booking_code)
        if err:
            return err
        rules = self.db["fare_rules"][b["fare"]]
        return {**{k: b[k] for k in ("booking_code", "passengers", "fare", "bags", "status",
                                     "paid")},
                "flights": [self._flight_view(f, b["booking_code"]) for f in b["flights"]],
                "change_fee": rules["change_fee"], "refundable_fare": rules["refundable"],
                "refund_eligible": self._eligible(b), "promo": b.get("promo")}

    def search_flights(self, origin: str = "", destination: str = "", date: str = "") -> dict:
        fs = [f for f in self.flights.values()
              if f["origin"] == origin.strip().upper()
              and f["destination"] == destination.strip().upper()
              and (not date or f["date"] == date) and f["status"] == "scheduled"
              and f["seats_left"] > 0]
        return {"flights": [{**self._flight_view(f["flight_no"]), "price": f["price"],
                             "fares": self.fare_prices(f["flight_no"])}
                            for f in sorted(fs, key=lambda x: (x["date"], x["depart"]))[:8]],
                "airports": self.db["airports"] if not fs else None}

    def fare_prices(self, flight_no: str, n: int = 1) -> dict[str, int]:
        p = self.flights[flight_no]["price"]
        return {fare: (m * p + add) * n for fare, (m, add) in FARE_PRICE.items()}

    def change_flight(self, booking_code: str = "", old_flight_no: str = "",
                      new_flight_no: str = "", customer_confirmed: bool = False) -> dict:
        b, err = self._booking(booking_code)
        if err:
            return err
        old = str(old_flight_no or "").strip().upper()
        if old not in b["flights"]:
            if len(b["flights"]) != 1 and old:
                return {"error": "not_in_booking", "changed": False,
                        "message": f"{old} is not in this booking; the change was NOT made.",
                        "booking_flights": [self._flight_view(f) for f in b["flights"]]}
            old = b["flights"][0]          # one-flight booking: no ambiguity
        o = self.flights[old]
        new = self.flights.get(str(new_flight_no).strip().upper())

        def alternatives() -> list[dict]:
            return self.search_flights(o["origin"], o["destination"])["flights"]
        if new is None or new["status"] != "scheduled" or new["seats_left"] < 1:
            return {"error": "unavailable", "changed": False, "message": "That flight does not "
                    "exist or is not available; the change was NOT made. Offer one of these.",
                    "alternatives": alternatives()}
        if (new["origin"], new["destination"]) != (o["origin"], o["destination"]):
            return {"error": "wrong_route", "changed": False, "message": "The new flight must "
                    "fly the same route; the change was NOT made.",
                    "alternatives": alternatives()}
        fee = self.db["fare_rules"][b["fare"]]["change_fee"] * len(b["passengers"])
        if not customer_confirmed:
            return {"confirmation_needed": True, "change_fee": fee, "from": self._flight_view(old),
                    "to": self._flight_view(new["flight_no"]),
                    "message": "Tell the customer the fee and ask them to confirm."}
        code = b["booking_code"]
        before = self._flight_view(old, code)
        b["flights"] = [new["flight_no"] if f == old else f for f in b["flights"]]
        after = self._flight_view(new["flight_no"], code)
        self._changed(code, "flight", before, after)
        return {"changed": True, "booking_code": code, "fee_charged": fee,
                "from": before, "to": after,
                "flights": [self._flight_view(f, code) for f in b["flights"]]}

    def cancel_booking(self, booking_code: str = "", customer_confirmed: bool = False) -> dict:
        b, err = self._booking(booking_code)
        if err:
            return err
        if b["status"] == "cancelled":
            return {"error": "already_cancelled"}
        if not customer_confirmed:
            return {"confirmation_needed": True, "refund_eligible": self._eligible(b),
                    "message": "Ask the customer to confirm the cancellation."}
        b["status"] = "cancelled"
        self._changed(b["booking_code"], "status", "confirmed", "cancelled")
        return {"cancelled": True, "booking_code": b["booking_code"],
                "refund_eligible": self._eligible(b)}

    def add_baggage(self, booking_code: str = "", bags: int = 1,
                    customer_confirmed: bool = False) -> dict:
        b, err = self._booking(booking_code)
        if err:
            return err
        try:
            bags = int(bags)
        except (TypeError, ValueError):
            bags = 0
        if not 1 <= bags <= 3:
            return {"error": "bad_bags", "message": "Between 1 and 3 extra bags."}
        price = bags * self.db["bag_price"]
        if not customer_confirmed:
            return {"confirmation_needed": True, "price": price,
                    "message": "Tell the customer the price and ask them to confirm."}
        self._changed(b["booking_code"], "bags", b["bags"], b["bags"] + bags)
        b["bags"] += bags
        return {"added": bags, "bags": b["bags"], "charged": price}

    def issue_refund(self, booking_code: str = "", customer_confirmed: bool = False) -> dict:
        b, err = self._booking(booking_code)
        if err:
            return err
        if not self._eligible(b):
            return {"refunded": False, "message": "Not eligible: Saver fares are not refundable "
                    "unless the airline cancelled a flight."}
        if b["status"] != "cancelled":
            return {"refunded": False, "message": "Cancel the booking first (with the "
                    "customer's confirmation)."}
        if b.get("refunded"):
            return {"refunded": False, "message": "Already refunded."}
        if not customer_confirmed:
            return {"confirmation_needed": True, "amount": b["paid"]}
        b["refunded"] = True
        self._changed(b["booking_code"], "refund", None, b["paid"])
        return {"refunded": True, "amount": b["paid"], "to": "original payment method"}

    def book_flight(self, flight_no: str = "", fare: str = "Saver", name: str = "",
                    email: str = "", passengers: int = 1,
                    customer_confirmed: bool = False) -> dict:
        """A new booking: for the verified customer, or for a NEW customer (name + an email
        the airline does not know yet). Quote first; book only with customer_confirmed."""
        f = self.flights.get(str(flight_no or "").strip().upper())
        if f is None or f["status"] != "scheduled" or f["seats_left"] < 1:
            return {"error": "unavailable", "booked": False, "message": "That flight does not "
                    "exist or is not bookable; nothing was booked. Search flights first."}
        fare_key = next((k for k in FARE_PRICE if k.lower() == str(fare or "").strip().lower()),
                        None)
        if fare_key is None:
            return {"error": "bad_fare", "booked": False, "fares": list(FARE_PRICE)}
        try:
            n = max(1, min(4, int(passengers or 1)))
        except (TypeError, ValueError):
            n = 1
        if self.verified:
            cid, cname = self.verified, self.customers[self.verified]["name"]
        else:
            email_n, name_n = str(email or "").strip().lower(), str(name or "").strip()
            if not name_n or "@" not in email_n:
                return {"error": "need_details", "booked": False, "message": "For a new "
                        "customer ask for their full name and email address."}
            if any(c["email"] == email_n for c in self.customers.values()):
                return {"error": "existing_customer", "booked": False, "message": "This email "
                        "belongs to an existing customer: verify them with verify_customer "
                        "first. Nothing was booked."}
            cid, cname = None, name_n.title()
        dup = next((c for c in self.codes_of(cid, active_only=True)
                    if f["flight_no"] in self.bookings[c]["flights"]), None) if cid else None
        if dup:
            return {"error": "duplicate", "booked": False, "booking_code": dup,
                    "message": f"The customer already has booking {dup} on this flight; "
                               "nothing new was booked."}
        price = self.fare_prices(f["flight_no"], n)[fare_key]
        rules = self.db["fare_rules"][fare_key]
        view = self._flight_view(f["flight_no"])
        if not customer_confirmed:
            return {"confirmation_needed": True, "flight": view, "fare": fare_key,
                    "passengers": n, "price": price, "change_fee": rules["change_fee"],
                    "refundable_fare": rules["refundable"],
                    "message": "Tell the customer the flight, fare and price and ask them "
                               "to confirm."}
        if cid is None:
            cid = f"C{2001 + len(self.new_customers)}"
            self.customers[cid] = {"customer_id": cid, "email": str(email).strip().lower(),
                                   "name": cname, "tier": "New"}
            self.new_customers.add(cid)
            self.verified = cid
        rnd = random.Random(f"{cid}:{f['flight_no']}:{len(self.bookings)}")
        code = ""
        while not code or code in self.bookings:
            code = "".join(rnd.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(6))
        self.bookings[code] = {"booking_code": code, "customer_id": cid, "fare": fare_key,
                               "flights": [f["flight_no"]], "paid": price, "bags": 0,
                               "passengers": [cname] + [f"Guest {i}" for i in range(2, n + 1)],
                               "status": ACTIVE,
                               "seats": {f["flight_no"]: seat_for(code, f["flight_no"])}}
        self.new_bookings.add(code)
        self._changed(code, "booking", None, "new")
        return {"booked": True, "booking_code": code, "customer_id": cid,
                "new_customer": cid in self.new_customers, "fare": fare_key, "paid": price,
                "flight": self._flight_view(f["flight_no"], code)}

    def list_offers(self) -> dict:
        return {"offers": OFFERS, "message": "Fictional current offers of Skypiea Air. Apply "
                "one with apply_promo (verified customer, a booking code)."}

    def apply_promo(self, booking_code: str = "", promo_code: str = "",
                    customer_confirmed: bool = False) -> dict:
        b, err = self._booking(booking_code)
        if err:
            return err
        o = next((x for x in OFFERS if x["promo_code"] == str(promo_code or "").strip().upper()),
                 None)
        if o is None:
            return {"error": "unknown_promo", "applied": False, "message": "No such offer; "
                    "nothing was applied.", "offers": [x["promo_code"] for x in OFFERS]}
        if b["status"] != ACTIVE:
            return {"applied": False, "message": "Only active bookings can get an offer."}
        if b.get("promo"):
            return {"applied": False, "message": f"This booking already has {b['promo']}."}
        months = {self.flights[f]["date"][:7] for f in b["flights"]}
        ok = {"SKYWINTER10": months == {"2026-12"} and b["paid"] >= 100,
              "BAG35": b["bags"] >= 1, "BIZ25": b["fare"] == "Business"}[o["promo_code"]]
        if not ok:
            return {"applied": False, "message": "This booking does not meet the conditions; "
                    "nothing was applied.", "conditions": o["conditions"]}
        discount = round(b["paid"] * o["value"] / 100) if o["kind"] == "pct" else o["value"]
        discount = min(discount, b["paid"])
        if not customer_confirmed:
            return {"confirmation_needed": True, "promo_code": o["promo_code"],
                    "discount": discount, "new_total": b["paid"] - discount,
                    "message": "Tell the customer the discount and ask them to confirm."}
        self._changed(b["booking_code"], "paid", b["paid"], b["paid"] - discount)
        b["paid"] -= discount
        b["promo"] = o["promo_code"]
        return {"applied": True, "booking_code": b["booking_code"], "promo_code": o["promo_code"],
                "discount": discount, "paid": b["paid"]}

    def sidebar(self) -> str | None:
        """What a CRM shows a support agent about the verified customer (facts only, from this
        session's database). None before verification."""
        cid = self.verified
        if not cid:
            return None
        c = self.customers[cid]
        lines = [f"Customer: {c['name']} ({cid}), verified"
                 + (", NEW customer (booked in this conversation)"
                    if cid in self.new_customers else f", tier {c.get('tier', '-')}") + "."]
        for code in self.codes_of(cid):
            b = self.bookings[code]
            r = self.db["fare_rules"][b["fare"]]
            fl = "; ".join(f"{v['flight_no']} {v['origin']}->{v['destination']} {v['date']} "
                           f"{v['depart']}-{v['arrive']} seat {v['seat']}"
                           + ("" if v["status"] == "scheduled" else f" ({v['status']})")
                           for v in (self._flight_view(x, code) for x in b["flights"]))
            lines.append(
                f"- {code}: {b['status']}, {b['fare']} fare (change fee ${r['change_fee']} per "
                f"passenger, {'refundable' if r['refundable'] else 'not refundable'}), "
                f"{len(b['passengers'])} passenger(s), {b['bags']} bag(s), paid ${b['paid']}"
                + (", refunded" if b.get("refunded") else "")
                + (f", promo {b['promo']}" if b.get("promo") else "") + f". Flights: {fl}.")
        lines.append(f"Extra checked bag: ${self.db['bag_price']} each (1-3 per booking).")
        return "\n".join(lines)

    def transfer_to_human(self, summary: str = "") -> dict:
        return {"transferred": True, "message": "A human colleague will take over."}

    def call(self, tool: str, args: dict[str, Any]) -> dict:
        fn = getattr(self, tool, None)
        if tool.startswith("_") or tool in ("call", "reset") or not callable(fn):
            return {"error": "unknown_tool"}
        try:
            return fn(**args)
        except TypeError as e:
            return {"error": "bad_arguments", "message": str(e)[:200]}


def personas(n: int = 6) -> list[dict]:
    """Demo personas for the presenter: customers with a unique name and at least one booking,
    each with an email, a booking code and two suggested requests (deterministic)."""
    db = pristine()
    names = [c["name"] for c in db["customers"]]
    out = []
    for c in db["customers"]:
        bs = [b for b in db["bookings"] if b["customer_id"] == c["customer_id"]
              and b["status"] == "confirmed"]
        if names.count(c["name"]) != 1 or not bs:
            continue
        b, f = None, None
        for bb in bs:      # a booking whose first flight has a later same-route alternative
            ff = next(x for x in db["flights"] if x["flight_no"] == bb["flights"][0])
            if any(x["origin"] == ff["origin"] and x["destination"] == ff["destination"]
                   and x["flight_no"] != ff["flight_no"] and x["status"] == "scheduled"
                   and x["seats_left"] > 0 and (x["date"], x["depart"]) > (ff["date"],
                                                                         ff["depart"])
                   for x in db["flights"]):
                b, f = bb, ff
                break
        if b is None:
            continue
        out.append({"name": c["name"], "email": c["email"], "booking_code": b["booking_code"],
                    "fare": b["fare"],
                    "requests": [f"Hi, I'm {c['name']}. Can I move my flight {f['flight_no']} "
                                 f"({f['origin']} to {f['destination']} on {f['date']}) to a "
                                 f"later flight?",
                                 f"I'm {c['name']}, booking {b['booking_code']}. Please add one "
                                 f"checked bag."]})
        if len(out) >= n:
            break
    amb = sorted({x for x in names if names.count(x) > 1})
    if amb:
        out.append({"name": amb[0], "note": "AMBIGUOUS on purpose: two customers share this "
                    "name; Skye must ask for an email or booking code", "requests": [
                        f"Hi, this is {amb[0]}. I need help with my booking."]})
    return out
