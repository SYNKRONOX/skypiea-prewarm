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
"""

from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).with_name("skyair_db.json")


@lru_cache(maxsize=1)
def pristine() -> dict:
    return json.loads(DB_PATH.read_text())


class Session:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        db = pristine()
        self.db = db
        self.bookings = {b["booking_code"]: copy.deepcopy(b) for b in db["bookings"]}
        self.customers = {c["customer_id"]: c for c in db["customers"]}
        self.flights = {f["flight_no"]: f for f in db["flights"]}
        self.verified: str | None = None

    # ---------------- helpers ----------------
    def _need(self, customer_id: str | None) -> dict | None:
        if not self.verified:
            return {"error": "not_verified", "message": "The customer is not verified yet. Ask "
                    "for their email address or a booking code and call verify_customer."}
        if customer_id and customer_id != self.verified:
            return {"error": "not_allowed", "message": "That customer is not the verified one."}
        return None

    def _booking(self, code: str) -> tuple[dict | None, dict | None]:
        b = self.bookings.get(str(code).strip().upper())
        if b is None:
            return None, {"error": "not_found", "message": f"No booking {code!r}."}
        err = self._need(b["customer_id"])
        return (None, err) if err else (b, None)

    def _flight_view(self, no: str) -> dict:
        f = self.flights[no]
        return {k: f[k] for k in ("flight_no", "origin", "destination", "date", "depart",
                                  "arrive", "status")}

    def _eligible(self, b: dict) -> bool:
        fare_ok = self.db["fare_rules"][b["fare"]]["refundable"]
        airline = any(self.flights[f]["status"] == "cancelled_by_airline" for f in b["flights"])
        return fare_ok or airline

    # ---------------- tools ----------------
    def find_customer(self, name: str = "") -> dict:
        hits = [c for c in self.customers.values() if c["name"].lower() == name.strip().lower()]
        if not hits:
            return {"found": 0, "message": f"No customer named {name!r}. Ask the customer to "
                    "spell their name."}
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
        return {"verified": True, "customer_id": found["customer_id"], "name": found["name"]}

    def get_customer_details(self, customer_id: str = "") -> dict:
        err = self._need(customer_id or self.verified)
        if err:
            return err
        c = self.customers[self.verified]
        return {**c, "bookings": sorted(code for code, b in self.bookings.items()
                                        if b["customer_id"] == c["customer_id"])}

    def get_booking(self, booking_code: str = "") -> dict:
        b, err = self._booking(booking_code)
        if err:
            return err
        rules = self.db["fare_rules"][b["fare"]]
        return {**{k: b[k] for k in ("booking_code", "passengers", "fare", "bags", "status",
                                     "paid")},
                "flights": [self._flight_view(f) for f in b["flights"]],
                "change_fee": rules["change_fee"], "refund_eligible": self._eligible(b)}

    def search_flights(self, origin: str = "", destination: str = "", date: str = "") -> dict:
        fs = [f for f in self.flights.values()
              if f["origin"] == origin.strip().upper()
              and f["destination"] == destination.strip().upper()
              and (not date or f["date"] == date) and f["status"] == "scheduled"
              and f["seats_left"] > 0]
        return {"flights": [{**self._flight_view(f["flight_no"]), "price": f["price"]}
                            for f in sorted(fs, key=lambda x: (x["date"], x["depart"]))[:8]],
                "airports": self.db["airports"] if not fs else None}

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
        b["flights"] = [new["flight_no"] if f == old else f for f in b["flights"]]
        return {"changed": True, "booking_code": b["booking_code"], "fee_charged": fee,
                "flights": [self._flight_view(f) for f in b["flights"]]}

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
        return {"refunded": True, "amount": b["paid"], "to": "original payment method"}

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
