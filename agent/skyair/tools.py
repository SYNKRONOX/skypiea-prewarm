"""Skypiea Air mock tools over the SYNTHETIC demo world (agent/skyair/world.py, fixed seed).

A ``Session`` holds one conversation's state: a private copy of the bookings (writes persist
within the session; ``reset()`` restores the world) and what the conversation has established
so far (STATE: verified customer, last search results, selected flight and booking, the action
waiting for the customer's yes). Tools never invent data: unknown ids give an explicit "not
found" that says what to do next (steering). Rules:
- find_customer(name): 0 matches -> not found (new customers can book); several -> ask for
  email or booking code; 1 -> customer id, still unverified. With an email or booking code as
  well, a single match is verified in the same call by verify_customer's rule.
- verify_customer: the email or a booking code must belong to the customer. An unknown email
  says so and names the exact next step for a new customer.
- Writes (book_flight, change_flight, cancel_booking, add_baggage, issue_refund, apply_promo)
  need ``customer_confirmed: true``; without it they return a quote (price, route, date) and
  the action is kept as PENDING until the customer says yes.
- Places: airport codes, city names and aliases ("Bangalore", "Bengaluru", "blr" -> BLR), with
  small typos tolerated. Dates: ISO, "20 Jan", "tomorrow", "next Friday", ... resolved against
  the demo-world today (world.ANCHOR), never the real date.
- Flights are identified by flight_id ("SKY214-20JAN"); a bare flight number is resolved with
  the date given or the last search.
- A booking tool called WITHOUT a booking code uses the selected booking, else the verified
  customer's only active booking; with several it lists them and asks which one.
- Money is in INR. Policy (change, cancel, refund, baggage): world.POLICY.
"""

from __future__ import annotations

import copy
import difflib
import random
import re
import zlib
from datetime import date, timedelta
from typing import Any

from agent.skyair import world as W

OFFERS = W.OFFERS
ACTIVE = "confirmed"
TODAY = W.ANCHOR
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
WRITE_TOOLS = ("book_flight", "change_flight", "cancel_booking", "add_baggage", "issue_refund",
               "apply_promo")


def pristine() -> dict:
    return W.build()


def money(v: Any) -> str:
    try:
        return f"INR {int(round(float(v))):,}"
    except (TypeError, ValueError):
        return f"INR {v}"


def seat_for(code: str, flight_id: str) -> str:
    """A fictional, deterministic seat for a booking on a flight."""
    h = zlib.crc32(f"{code}:{flight_id}".encode())
    return f"{5 + h % 26}{'ABCDEF'[(h // 26) % 6]}"


# ------------------------------------------------------------------ resolution
_ALIAS: dict[str, str] = {}


def aliases() -> dict[str, str]:
    if not _ALIAS:
        for code, (city, name, *_r, al) in W.AIRPORTS.items():
            for k in (code, city, name, *al):
                _ALIAS[k.lower()] = code
    return _ALIAS


def resolve_airport(text: Any) -> str | None:
    """'Bangalore', 'Bengaluru', 'blr', 'BLR', 'Bengaluru (BLR)', 'bangalor' -> 'BLR'."""
    t = re.sub(r"\s+", " ", str(text or "").strip().lower())
    if not t:
        return None
    al = aliases()
    if t in al:
        return al[t]
    m = re.search(r"\(([a-z]{3})\)", t)
    if m and m.group(1) in al:
        return al[m.group(1)]
    t2 = re.sub(r"\b(airport|international|intl|city|in india)\b", "", t).strip(" ,.")
    if t2 in al:
        return al[t2]
    for part in re.split(r"[,/-]", t2):
        if part.strip() in al:
            return al[part.strip()]
    near = difflib.get_close_matches(t2, [k for k in al if len(k) > 3], n=1, cutoff=0.8)
    return al[near[0]] if near else None


def resolve_date(text: Any, today: date = TODAY) -> str | None:
    """ISO, '20 Jan', 'Jan 20', '20/01', 'today', 'tomorrow', 'day after tomorrow', 'friday',
    'next friday', 'this friday', 'in 3 days' -> ISO date (demo-world today)."""
    t = str(text or "").strip().lower().rstrip(".")
    if not t:
        return None
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", t)
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3])).isoformat()
        except ValueError:
            return None
    if t in ("today", "tonight"):
        return today.isoformat()
    if t == "tomorrow":
        return (today + timedelta(days=1)).isoformat()
    if t in ("day after tomorrow", "the day after tomorrow"):
        return (today + timedelta(days=2)).isoformat()
    m = re.fullmatch(r"in (\d{1,2}) days?", t)
    if m:
        return (today + timedelta(days=int(m[1]))).isoformat()
    m = re.fullmatch(r"(next|this|coming)?\s*(monday|tuesday|wednesday|thursday|friday|"
                     r"saturday|sunday)", t)
    if m:
        wd = WEEKDAYS.index(m[2])
        ahead = (wd - today.weekday()) % 7 or 7      # "next friday" = the coming Friday
        return (today + timedelta(days=ahead)).isoformat()
    t = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", t).replace(",", " ")
    t = re.sub(r"^(mon|tue|wed|thu|fri|sat|sun)[a-z]*\s+", "", t)
    m = re.fullmatch(r"(\d{1,2})\s+([a-z]{3})[a-z]*(?:\s+(\d{4}))?", t) or None
    d = mo = y = None
    if m:
        d, mo, y = int(m[1]), m[2], m[3]
    else:
        m = re.fullmatch(r"([a-z]{3})[a-z]*\s+(\d{1,2})(?:\s+(\d{4}))?", t)
        if m:
            d, mo, y = int(m[2]), m[1], m[3]
    if mo is not None and mo in MONTHS:
        month = MONTHS.index(mo) + 1
    else:
        m = re.fullmatch(r"(\d{1,2})[/.](\d{1,2})(?:[/.](\d{2,4}))?", t)
        if not m:
            return None
        d, month, y = int(m[1]), int(m[2]), m[3]
    try:
        year = int(y) if y else today.year
        year = year + 2000 if year < 100 else year
        out = date(year, month, d)
        if not y and out < today:
            out = date(year + 1, month, d)
        return out.isoformat()
    except ValueError:
        return None


def say_date(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{WEEKDAYS[d.weekday()].title()} {d.day} {d.strftime('%B')}"


class Session:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        db = pristine()
        self.db = db
        self.today = TODAY
        self.bookings = {b["booking_code"]: copy.deepcopy(b) for b in db["bookings"]}
        self.customers = {c["customer_id"]: c for c in db["customers"]}
        self.flights = db["flight_index"]
        self.verified: str | None = None
        self.new_customers: set[str] = set()
        self.new_bookings: set[str] = set()
        self.changes: list[dict] = []
        self.sidebar_due = False
        self.transferred = False
        # conversation STATE (re-injected into Skye's prompt every turn)
        self.last_search: list[str] = []         # flight ids, best first (top 5)
        self.last_query: dict[str, str] = {}
        self.selected_flight: str | None = None
        self.selected_booking: str | None = None
        self.pending: dict[str, Any] | None = None

    # ---------------- helpers for the suite / console ----------------
    def place_name(self, code: str, alt: int = 0) -> str:
        city, _n, *_r, al = W.AIRPORTS[code]
        return al[0].title() if alt and al else city

    def say_date(self, iso: str) -> str:
        return say_date(iso)

    def promo_for(self, flight_id: str, fare: str) -> str | None:
        f = self.flights[flight_id]
        price = W.fare_price(f["price"], fare)
        b = {"flights": [flight_id], "paid": price, "bags": 0, "fare": fare}
        return next((o["promo_code"] for o in OFFERS if self._promo_ok(o["promo_code"], b)),
                    None)

    def _need(self, customer_id: str | None) -> dict | None:
        if not self.verified:
            return {"error": "not_verified", "message": "The customer is not verified yet. "
                    "Next step: ask for their email address (or a booking code) and call "
                    "verify_customer(email=...)."}
        if customer_id and customer_id != self.verified:
            return {"error": "not_allowed", "message": "That customer is not the verified one."}
        return None

    def codes_of(self, cid: str | None, active_only: bool = False) -> list[str]:
        return sorted(code for code, b in self.bookings.items() if b["customer_id"] == cid
                      and (not active_only or b["status"] == ACTIVE))

    def _booking(self, code: str | None) -> tuple[dict | None, dict | None]:
        code = str(code or "").strip().upper().replace(" ", "")
        if not code:
            err = self._need(None)
            if err:
                return None, err
            if self.selected_booking and self.bookings[self.selected_booking]["customer_id"] \
                    == self.verified:
                return self.bookings[self.selected_booking], None
            active = self.codes_of(self.verified, active_only=True)
            if len(active) == 1:
                return self.bookings[active[0]], None
            if not active:
                return None, {"error": "no_active_booking", "message": "This customer has "
                              "no active booking.", "your_bookings": self.codes_of(
                                  self.verified)}
            return None, {"error": "which_booking", "message": "The customer has several "
                          "active bookings: ask which one (nothing was done).",
                          "your_bookings": [self._brief(c) for c in active]}
        b = self.bookings.get(code)
        if b is None:
            err = self._need(None)
            if err:
                return None, {**err, "detail": f"No booking {code!r}."}
            return None, {"error": "not_found", "message": f"No booking {code!r}; nothing was "
                          "done. Use one of the customer's booking codes.",
                          "your_bookings": [self._brief(c) for c in self.codes_of(
                              self.verified, active_only=True)]}
        err = self._need(b["customer_id"])
        if err and self.verified:
            err = {**err, "your_bookings": self.codes_of(self.verified)}
        if not err:
            self.selected_booking = code
        return (None, err) if err else (b, None)

    def _brief(self, code: str) -> str:
        b = self.bookings[code]
        f = self.flights[b["flights"][0]]
        return f"{code} ({f['origin']}->{f['destination']} {f['date']}, {b['status']})"

    def _flight_view(self, fid: str, code: str | None = None) -> dict:
        f = self.flights[fid]
        v = {k: f[k] for k in ("flight_id", "flight_no", "origin", "destination", "date",
                               "depart", "arrive", "status")}
        v["duration"] = f"{f['duration_min'] // 60}h {f['duration_min'] % 60:02d}m"
        if code:
            b = self.bookings.get(code) or {}
            v["seat"] = (b.get("seats") or {}).get(fid) or seat_for(code, fid)
        return v

    def _changed(self, code: str, what: str, old: Any, new: Any) -> None:
        self.changes.append({"booking_code": code, "what": what, "old": old, "new": new})
        self.sidebar_due = True
        self.pending = None
        self.selected_booking = code

    def _intl(self, b: dict) -> bool:
        return any(self.flights[f]["international"] for f in b["flights"])

    def _eligible(self, b: dict) -> bool:
        fare_ok = W.FARE_RULES[b["fare"]]["refundable"]
        airline = any(self.flights[f]["status"] == "cancelled_by_airline" for f in b["flights"])
        return fare_ok or airline

    def _refund_amount(self, b: dict) -> int:
        if any(self.flights[f]["status"] == "cancelled_by_airline" for f in b["flights"]):
            return b["paid"]
        fee = (W.FARE_RULES[b["fare"]]["cancel_fee"] or 0) * len(b["passengers"])
        return max(0, b["paid"] - fee)

    def resolve_flight(self, ref: Any, when: Any = None) -> dict | None:
        """flight_id, or a flight number with a date (or the last search)."""
        t = str(ref or "").strip().upper().replace(" ", "")
        if not t:
            return None
        if t in self.flights:
            return self.flights[t]
        m = re.fullmatch(r"(SKY\d{3,4})(?:[-/](\d{1,2}[A-Z]{3}))?", t) or \
            re.fullmatch(r"(\d{3,4})", t)
        if not m:
            return None
        no = m.group(1) if m.group(1).startswith("SKY") else "SKY" + m.group(1)
        if m.lastindex and m.lastindex >= 2 and m.group(2):
            fid = f"{no}-{m.group(2)}"
            return self.flights.get(fid)
        iso = resolve_date(when, self.today) if when else None
        if iso:
            return self.flights.get(f"{no}-{date.fromisoformat(iso).strftime('%d%b').upper()}")
        hits = [self.flights[x] for x in self.last_search if self.flights[x]["flight_no"] == no]
        return hits[0] if len(hits) == 1 else None

    def fare_prices(self, fid: str, n: int = 1) -> dict[str, int]:
        p = self.flights[fid]["price"]
        return {fare: W.fare_price(p, fare) * n for fare in W.FARES}

    def bag_price(self, b: dict) -> int:
        return W.BAG_PRICE["international" if self._intl(b) else "domestic"]

    def _quote(self, tool: str, args: dict, summary: str, **extra) -> dict:
        self.pending = {"tool": tool, "args": {k: v for k, v in args.items()
                                               if k != "customer_confirmed"},
                        "summary": summary}
        return {"confirmation_needed": True, "summary": summary, **extra,
                "message": "Tell the customer exactly this summary and ask them to confirm "
                           "with yes. Only after their yes, call the same tool again with "
                           "customer_confirmed=true."}

    # ---------------- tools ----------------
    def find_customer(self, name: str = "", email: str = "", booking_code: str = "") -> dict:
        res = self._find(name)
        if res.get("found") == 1 and (email or booking_code):
            v = self.verify_customer(name=name, email=email, booking_code=booking_code)
            return {**res, **v, "message": "Found and verified." if v.get("verified") else
                    v["message"]}
        if res.get("found") == 0 and email:
            v = self.verify_customer(email=email)
            if v.get("verified"):
                return {**v, "message": "Found by email (the name differs slightly) and "
                        "verified."}
        return res

    def _find(self, name: str = "") -> dict:
        n = " ".join(str(name or "").split()).lower()
        hits = [c for c in self.customers.values() if c["name"].lower() == n]
        if not hits:
            return {"found": 0, "message": f"No customer named {name!r}. Ask them to check "
                    "the spelling or give their email. If they are NEW to Skypiea Air, offer "
                    "to create their profile by booking: call search_flights(origin, "
                    "destination, date), then book_flight(flight_id, fare, name, email)."}
        if len(hits) > 1:
            return {"found": len(hits), "ambiguous": True, "message": "Several customers have "
                    "this name. Ask for their email address or a booking code to verify."}
        return {"found": 1, "customer_id": hits[0]["customer_id"], "verified": False,
                "message": "Not verified yet. If the customer ALREADY gave an email address or "
                           "a booking code, call verify_customer with it now (do not ask "
                           "again); otherwise ask for one."}

    def verify_customer(self, name: str = "", customer_id: str = "", email: str = "",
                        booking_code: str = "") -> dict:
        refuse = {"verified": False, "message": "I could not verify this customer with those "
                  "details. Politely say so and ask them to check the email or booking code; "
                  "do not share any account information."}
        found = None
        em = str(email or "").strip().lower()
        if em:
            found = next((c for c in self.customers.values() if c["email"] == em), None)
            if found is None and not booking_code:
                return {**refuse, "error": "email_not_found", "message": f"No customer has "
                        f"the email {em!r}. Ask them to check it. If they are a NEW customer, "
                        "offer to create a profile by booking: call search_flights(origin, "
                        "destination, date), then book_flight(flight_id, fare, name, "
                        "email)."}
        if found is None and booking_code:
            b = self.bookings.get(str(booking_code).strip().upper())
            found = self.customers.get(b["customer_id"]) if b else None
        if found is None:
            return refuse
        if name and " ".join(str(name).split()).lower() != found["name"].lower():
            return refuse
        if customer_id and customer_id in self.customers and customer_id != found["customer_id"]:
            return refuse
        note = None
        if em and booking_code:
            b = self.bookings.get(str(booking_code).strip().upper())
            if em != found["email"] or (b is not None and b["customer_id"] !=
                                        found["customer_id"]):
                return refuse
            if b is None:      # a mistyped code: the email alone is the proof
                note = (f"Booking code {str(booking_code).upper()!r} does not exist; verified "
                        "by email. Use one of the active bookings below.")
                booking_code = ""
        self.verified = found["customer_id"]
        self.sidebar_due = True
        active = self.codes_of(found["customer_id"], active_only=True)
        if booking_code and str(booking_code).strip().upper() in self.bookings:
            self.selected_booking = str(booking_code).strip().upper()
        elif len(active) == 1:
            self.selected_booking = active[0]
        return {"verified": True, "customer_id": found["customer_id"], "name": found["name"],
                "active_bookings": [self._brief(c) for c in active],
                **({"note": note} if note else {})}

    def get_customer_details(self, customer_id: str = "") -> dict:
        err = self._need(customer_id or self.verified)
        if err:
            return err
        c = self.customers[self.verified]
        return {**{k: c[k] for k in ("customer_id", "name", "tier")},
                "bookings": [self._brief(x) for x in self.codes_of(c["customer_id"])]}

    def get_booking(self, booking_code: str = "") -> dict:
        b, err = self._booking(booking_code)
        if err:
            return err
        rules = W.FARE_RULES[b["fare"]]
        intl = self._intl(b)
        return {**{k: b[k] for k in ("booking_code", "passengers", "fare", "bags", "status",
                                     "paid", "currency")},
                "flights": [self._flight_view(f, b["booking_code"]) for f in b["flights"]],
                "change_fee_per_passenger": rules["change_fee_intl" if intl else "change_fee"],
                "refundable_fare": rules["refundable"],
                "refund_eligible": self._eligible(b), "refund_amount_if_cancelled":
                    self._refund_amount(b) if self._eligible(b) else 0,
                "checked_baggage_included_kg": rules["checked_kg"],
                "extra_bag_price": self.bag_price(b), "promo": b.get("promo"),
                "refunded": bool(b.get("refunded"))}

    def search_flights(self, origin: str = "", destination: str = "", date: str = "") -> dict:
        o, d = resolve_airport(origin), resolve_airport(destination)
        bad = [x for x, c in ((origin, o), (destination, d)) if not c]
        if bad:
            return {"error": "unknown_airport", "flights": [], "message": f"Unknown place "
                    f"{', '.join(repr(str(x)) for x in bad)}. Ask the customer which city they "
                    "mean (Skypiea Air flies to 38 Indian cities and DXB, DOH, AUH, SIN, BKK, "
                    "KUL, CMB, LHR)."}
        if o == d:
            return {"error": "same_airport", "flights": [], "message": "Origin and "
                    "destination are the same."}
        iso = resolve_date(date, self.today) if date else None
        if date and not iso:
            return {"error": "bad_date", "flights": [], "message": f"Could not read the date "
                    f"{date!r}. Ask the customer for a date like 20 January (today is "
                    f"{say_date(self.today.isoformat())})."}
        if iso and iso < self.today.isoformat():
            return {"error": "past_date", "flights": [], "message": f"{iso} is in the past "
                    f"(today is {self.today.isoformat()})."}
        fs = [f for f in self.flights.values() if f["origin"] == o and f["destination"] == d
              and f["date"] >= self.today.isoformat() and f["status"] == "scheduled"
              and f["seats_left"] > 0]
        if not fs:
            served = sorted({f["destination"] for f in self.flights.values()
                             if f["origin"] == o})
            return {"flights": [], "origin": o, "destination": d, "message": f"Skypiea Air "
                    f"has no nonstop flight {o}->{d}. From {o} it flies to: "
                    f"{', '.join(served)}."}
        day = [f for f in fs if f["date"] == iso] if iso else []
        note = None
        if iso and not day:
            near = sorted({f["date"] for f in fs}, key=lambda x: (abs(
                (date.fromisoformat(x) - date.fromisoformat(iso)).days), x))[:3]
            day = [f for f in fs if f["date"] in near]
            note = f"No seats on {iso}; nearest dates shown."
        elif not iso:
            day = fs
        top = sorted(day, key=lambda f: (f["date"], f["depart"]))[:5]
        self.last_search = [f["flight_id"] for f in top]
        self.last_query = {"origin": o, "destination": d, "date": iso or ""}
        self.selected_flight = top[0]["flight_id"] if len(top) == 1 else self.selected_flight
        return {"origin": f"{o} ({W.AIRPORTS[o][0]})",
                "destination": f"{d} ({W.AIRPORTS[d][0]})", "date": iso, "currency": "INR",
                "flights": [{**self._flight_view(f["flight_id"]), "seats_left": f["seats_left"],
                             "fares": self.fare_prices(f["flight_id"])} for f in top],
                **({"note": note} if note else {})}

    def change_flight(self, booking_code: str = "", old_flight_no: str = "",
                      new_flight_no: str = "", customer_confirmed: bool = False,
                      new_date: str = "") -> dict:
        b, err = self._booking(booking_code)
        if err:
            return err
        if b["status"] != ACTIVE:
            return {"error": "not_active", "changed": False, "message": f"Booking is "
                    f"{b['status']}; it cannot be changed."}
        old = None
        if old_flight_no:
            of = self.resolve_flight(old_flight_no)
            old = of["flight_id"] if of and of["flight_id"] in b["flights"] else None
            if old is None and len(b["flights"]) == 1:
                old = b["flights"][0]
            elif old is None:
                return {"error": "not_in_booking", "changed": False,
                        "message": f"{old_flight_no} is not in this booking; nothing changed.",
                        "booking_flights": [self._flight_view(f) for f in b["flights"]]}
        elif len(b["flights"]) == 1:
            old = b["flights"][0]
        else:
            return {"error": "which_flight", "changed": False, "message": "This booking has "
                    "several flights: ask which one to change (old_flight_no).",
                    "booking_flights": [self._flight_view(f) for f in b["flights"]]}
        o = self.flights[old]
        new = self.resolve_flight(new_flight_no, new_date) if new_flight_no else None
        others: list[dict] = []
        if new is None and new_date and not new_flight_no:
            # only a date: quote the EARLIEST bookable flight that day, list the others
            res = self.search_flights(o["origin"], o["destination"], new_date)
            opts = [x for x in res.get("flights", []) if x["date"] == resolve_date(
                new_date, self.today)]
            if not opts:
                return {"error": "no_flight_that_day", "changed": False, "message": res.get(
                    "message") or "No flight that day on this route; offer these instead.",
                    "options": res.get("flights", [])}
            new, others = self.flights[opts[0]["flight_id"]], opts[1:]

        def alternatives() -> list[dict]:
            return self.search_flights(o["origin"], o["destination"],
                                       new_date or "")["flights"]
        if new is None or new["status"] != "scheduled" or new["seats_left"] < 1:
            return {"error": "unavailable", "changed": False, "message": "That flight does not "
                    "exist or is not available; nothing changed. Offer one of these (use its "
                    "flight_id).", "alternatives": alternatives()}
        if (new["origin"], new["destination"]) != (o["origin"], o["destination"]):
            return {"error": "wrong_route", "changed": False, "message": "The new flight must "
                    "fly the same route; nothing changed.", "alternatives": alternatives()}
        if new["date"] < self.today.isoformat():
            return {"error": "past_date", "changed": False}
        rules = W.FARE_RULES[b["fare"]]
        n = len(b["passengers"])
        fee = rules["change_fee_intl" if self._intl(b) else "change_fee"] * n
        diff = max(0, (W.fare_price(new["price"], b["fare"]) -
                       W.fare_price(o["price"], b["fare"])) * n)
        total = fee + diff
        self.selected_flight = new["flight_id"]
        if not customer_confirmed:
            return self._quote(
                "change_flight", {"booking_code": b["booking_code"], "old_flight_no": old,
                                  "new_flight_no": new["flight_id"]},
                f"Move booking {b['booking_code']} from {o['flight_no']} on {o['date']} "
                f"{o['depart']} to {new['flight_no']} on {new['date']} {new['depart']} "
                f"({o['origin']}->{o['destination']}): change fee {money(fee)} + fare "
                f"difference {money(diff)} = {money(total)}.",
                change_fee=fee, fare_difference=diff, total=total,
                other_flights_that_day=[f"{x['flight_id']} {x['depart']}" for x in others],
                **{"from": self._flight_view(old), "to": self._flight_view(new["flight_id"])})
        code = b["booking_code"]
        before = self._flight_view(old, code)
        b["flights"] = [new["flight_id"] if f == old else f for f in b["flights"]]
        b["paid"] += total
        after = self._flight_view(new["flight_id"], code)
        self._changed(code, "flight", before, after)
        return {"changed": True, "booking_code": code, "charged": total, "fee_charged": fee,
                "fare_difference": diff, "from": before, "to": after,
                "flights": [self._flight_view(f, code) for f in b["flights"]]}

    def cancel_booking(self, booking_code: str = "", customer_confirmed: bool = False) -> dict:
        b, err = self._booking(booking_code)
        if err:
            return err
        if b["status"] != ACTIVE:
            return {"error": f"already_{b['status']}", "cancelled": False,
                    "message": f"Booking is {b['status']}; nothing to cancel."}
        el = self._eligible(b)
        amt = self._refund_amount(b) if el else 0
        if not customer_confirmed:
            f = self.flights[b["flights"][0]]
            return self._quote("cancel_booking", {"booking_code": b["booking_code"]},
                               f"Cancel booking {b['booking_code']} ({f['origin']}->"
                               f"{f['destination']} on {f['date']}, {b['fare']} fare). "
                               + (f"Refund after cancelling: {money(amt)} to the original "
                                  "payment method (with issue_refund)." if el else
                                  "Saver fare: no refund."),
                               refund_eligible=el, refund_amount=amt)
        b["status"] = "cancelled"
        self._changed(b["booking_code"], "status", "confirmed", "cancelled")
        return {"cancelled": True, "booking_code": b["booking_code"], "refund_eligible": el,
                "refund_amount": amt, "message": ("Now call issue_refund for this booking if "
                                                  "the customer wants the refund.") if el
                else "Not refundable (Saver fare)."}

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
        if b["status"] != ACTIVE:
            return {"error": "not_active", "added": 0, "message": f"Booking is {b['status']}."}
        price = bags * self.bag_price(b) * len(b["passengers"])
        if not customer_confirmed:
            return self._quote("add_baggage", {"booking_code": b["booking_code"],
                                               "bags": bags},
                               f"Add {bags} extra 15 kg checked bag(s) per passenger to "
                               f"booking {b['booking_code']}: {money(price)}.", price=price)
        self._changed(b["booking_code"], "bags", b["bags"], b["bags"] + bags)
        b["bags"] += bags
        b["paid"] += price
        return {"added": bags, "bags": b["bags"], "charged": price}

    def issue_refund(self, booking_code: str = "", customer_confirmed: bool = False,
                     **_ignored: Any) -> dict:
        b, err = self._booking(booking_code)
        if err:
            return err
        if not self._eligible(b):
            return {"refunded": False, "message": "Not eligible: Saver fares are not refundable "
                    "unless Skypiea Air cancelled a flight."}
        if b["status"] != "cancelled":
            return {"refunded": False, "error": "cancel_first", "message": "Cancel the "
                    "booking first: call cancel_booking (with the customer's confirmation)."}
        if b.get("refunded"):
            return {"refunded": False, "message": "Already refunded."}
        amt = self._refund_amount(b)
        if not customer_confirmed:
            return self._quote("issue_refund", {"booking_code": b["booking_code"]},
                               f"Refund {money(amt)} for booking {b['booking_code']} to the "
                               "original payment method.", amount=amt)
        b["refunded"] = True
        self._changed(b["booking_code"], "refund", None, amt)
        return {"refunded": True, "amount": amt, "to": "original payment method"}

    def book_flight(self, flight_no: str = "", fare: str = "Saver", name: str = "",
                    email: str = "", passengers: int = 1, customer_confirmed: bool = False,
                    date: str = "", promo_code: str = "") -> dict:
        f = self.resolve_flight(flight_no, date)
        if f is None:
            q = self.last_query
            hint = (f"call search_flights(origin='{q['origin']}', destination="
                    f"'{q['destination']}', date='{q['date']}') again and use a flight_id "
                    "from it" if q else "search first: call search_flights(origin, "
                    "destination, date) and use a flight_id from the results")
            return {"error": "unknown_flight", "booked": False, "message": f"Flight "
                    f"{flight_no!r} not found; nothing was booked. Next step: {hint}."}
        if f["status"] != "scheduled" or f["seats_left"] < 1 or \
                f["date"] < self.today.isoformat():
            return {"error": "unavailable", "booked": False, "message": "That flight is not "
                    "bookable; nothing was booked. Search again."}
        fare_key = next((k for k in W.FARES if k.lower() == str(fare or "").strip().lower()),
                        None)
        if fare_key is None:
            return {"error": "bad_fare", "booked": False, "fares": list(W.FARES)}
        try:
            n = max(1, min(4, int(passengers or 1)))
        except (TypeError, ValueError):
            n = 1
        self.selected_flight = f["flight_id"]
        if self.verified:
            cid, cname = self.verified, self.customers[self.verified]["name"]
        else:
            email_n, name_n = str(email or "").strip().lower(), " ".join(str(name or "").split())
            if not name_n or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", email_n):
                return {"error": "need_details", "booked": False, "message": "For a new "
                        "customer ask for their full name and a valid email address, then "
                        "call book_flight again with name and email."}
            if any(c["email"] == email_n for c in self.customers.values()):
                return {"error": "existing_customer", "booked": False, "message": "This email "
                        "belongs to an existing customer: verify them with "
                        f"verify_customer(email='{email_n}') first. Nothing was booked."}
            cid, cname = None, name_n.title()
        dup = next((c for c in self.codes_of(cid, active_only=True)
                    if f["flight_id"] in self.bookings[c]["flights"]), None) if cid else None
        if dup:
            return {"error": "duplicate", "booked": False, "booking_code": dup,
                    "message": f"The customer already has booking {dup} on this flight; "
                               "nothing new was booked."}
        price = self.fare_prices(f["flight_id"], n)[fare_key]
        rules = W.FARE_RULES[fare_key]
        view = self._flight_view(f["flight_id"])
        promo, disc, promo_note = None, 0, None
        if promo_code:
            o = next((x for x in OFFERS if x["promo_code"] == str(promo_code).strip().upper()),
                     None)
            probe = {"flights": [f["flight_id"]], "paid": price, "bags": 0, "fare": fare_key}
            if o is None:
                promo_note = f"No offer {str(promo_code).upper()!r}; booked without a promo."
            elif not self._promo_ok(o["promo_code"], probe):
                promo_note = (f"{o['promo_code']} does not apply to this trip "
                              f"({o['conditions']}); booked without it.")
            else:
                promo = o["promo_code"]
                disc = round(price * o["value"] / 100) if o["kind"] == "pct" else o["value"]
                disc = min(disc, price)
        if not customer_confirmed:
            args = {"flight_no": f["flight_id"], "fare": fare_key, "passengers": n,
                    **({"promo_code": promo} if promo else {})}
            if cid is None:
                args.update(name=cname, email=str(email).strip().lower())
            return self._quote(
                "book_flight", args,
                f"Book {f['flight_no']} {f['origin']}->{f['destination']} on {f['date']} "
                f"{f['depart']}-{f['arrive']}, {fare_key} fare, {n} passenger(s): "
                f"{money(price)}" + (f", with {promo} -{money(disc)} = {money(price - disc)}"
                                     if promo else "")
                + ("" if cid else f", for {cname} ({email})") + "."
                + (f" Note: {promo_note}" if promo_note else ""),
                flight=view, fare=fare_key, passengers=n, price=price - disc,
                change_fee=rules["change_fee_intl" if f["international"] else "change_fee"],
                refundable_fare=rules["refundable"])
        if cid is None:
            cid = f"C{9001 + len(self.new_customers)}"
            self.customers = dict(self.customers)
            self.customers[cid] = {"customer_id": cid, "email": str(email).strip().lower(),
                                   "name": cname, "tier": "New", "phone": ""}
            self.new_customers.add(cid)
            self.verified = cid
        rnd = random.Random(f"{cid}:{f['flight_id']}:{len(self.bookings)}")
        code = ""
        while not code or code in self.bookings:
            code = "".join(rnd.choice(W.CODE_ALPHABET) for _ in range(6))
        self.bookings[code] = {"booking_code": code, "customer_id": cid, "fare": fare_key,
                               "flights": [f["flight_id"]], "paid": price, "bags": 0,
                               "passengers": [cname] + [f"Guest {i}" for i in range(2, n + 1)],
                               "status": ACTIVE, "currency": "INR",
                               "seats": {f["flight_id"]: seat_for(code, f["flight_id"])}}
        self.new_bookings.add(code)
        self._changed(code, "booking", None, "new")
        if promo:
            self.bookings[code]["paid"] = price - disc
            self.bookings[code]["promo"] = promo
            self._changed(code, "paid", price, price - disc)
        return {"booked": True, "booking_code": code, "customer_id": cid,
                **({"promo_applied": promo, "discount": disc} if promo else {}),
                **({"promo_note": promo_note} if promo_note else {}),
                "new_customer": cid in self.new_customers, "fare": fare_key,
                "paid": price - disc,
                "currency": "INR", "flight": self._flight_view(f["flight_id"], code)}

    def list_offers(self) -> dict:
        return {"offers": OFFERS, "message": "Fictional current offers of Skypiea Air. Apply "
                "one with apply_promo (verified customer, a booking)."}

    def _promo_ok(self, code: str, b: dict) -> bool:
        fl = [self.flights[x] for x in b["flights"]]
        if code == "SKYWINTER10":
            return all("2027-01-11" <= f["date"] <= "2027-02-28" for f in fl) and \
                b["paid"] >= 3000
        if code == "GOAWEEKEND":
            return b["fare"] == "Saver" and any(f["destination"] == "GOI" for f in fl) and all(
                date.fromisoformat(f["date"]).weekday() in (4, 5) for f in fl
                if f["destination"] == "GOI")
        if code == "BAG500":
            return b["bags"] >= 1
        if code == "BIZ1500":
            return b["fare"] == "Business"
        return False

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
        if not self._promo_ok(o["promo_code"], b):
            return {"applied": False, "message": "This booking does not meet the conditions; "
                    "nothing was applied.", "conditions": o["conditions"]}
        discount = round(b["paid"] * o["value"] / 100) if o["kind"] == "pct" else o["value"]
        discount = min(discount, b["paid"])
        if not customer_confirmed:
            return self._quote("apply_promo", {"booking_code": b["booking_code"],
                                               "promo_code": o["promo_code"]},
                               f"Apply {o['promo_code']} to booking {b['booking_code']}: "
                               f"{money(discount)} off, new total {money(b['paid'] - discount)}.",
                               promo_code=o["promo_code"], discount=discount,
                               new_total=b["paid"] - discount)
        self._changed(b["booking_code"], "paid", b["paid"], b["paid"] - discount)
        b["paid"] -= discount
        b["promo"] = o["promo_code"]
        return {"applied": True, "booking_code": b["booking_code"], "promo_code": o["promo_code"],
                "discount": discount, "paid": b["paid"]}

    def transfer_to_human(self, summary: str = "", **_ignored: Any) -> dict:
        self.transferred = True
        self.pending = None
        return {"transferred": True, "message": "A human colleague will take over. Tell the "
                "customer briefly."}

    # ---------------- state, gating, sidebar ----------------
    def allowed_tools(self) -> set[str]:
        """Tool gating (guardrail): which tools Skye is offered right now."""
        out = {"find_customer", "verify_customer", "search_flights", "list_offers",
               "transfer_to_human"}
        if self.last_search or self.selected_flight:
            out.add("book_flight")
        if self.verified:
            out |= {"get_customer_details", "get_booking"}
            if self._booking("")[0] is not None or self.selected_booking:
                out |= {"change_flight", "cancel_booking", "add_baggage", "issue_refund",
                        "apply_promo"}
        return out

    def why_not(self, tool: str) -> str:
        """Steering message for a tool that is not offered yet (names the next call)."""
        if tool == "book_flight":
            return ("Search first: call search_flights(origin=..., destination=..., "
                    "date=...) and then book_flight with a flight_id from the results.")
        if not self.verified:
            return ("Verify the customer first: call verify_customer(email=...) with the "
                    "email they gave (or a booking code). New customers can only book a "
                    "flight (search_flights, then book_flight with name and email).")
        return ("Select the booking first: ask which booking and call "
                "get_booking(booking_code=...). The customer's active bookings: "
                + ", ".join(self._brief(c) for c in self.codes_of(self.verified, True)) + ".")

    def state_block(self) -> str:
        lines = [f"Demo-world today: {say_date(self.today.isoformat())} "
                 f"({self.today.isoformat()}); times are IST; money is INR."]
        if self.verified:
            c = self.customers[self.verified]
            act = self.codes_of(self.verified, active_only=True)
            brief = ", ".join(self._brief(x) for x in act) or "none"
            lines.append(f"Verified customer: {c['name']} ({self.verified})"
                         + (", NEW customer" if self.verified in self.new_customers else "")
                         + f"; active bookings: {brief}.")
        else:
            lines.append("Verified customer: none yet.")
        if self.last_search:
            q = self.last_query
            lines.append(f"Last search {q.get('origin')}->{q.get('destination')} "
                         f"{q.get('date') or '(any date)'} (top {len(self.last_search)}):")
            for fid in self.last_search:
                f = self.flights[fid]
                p = self.fare_prices(fid)
                lines.append(f"  - {fid}: {f['date']} {f['depart']}-{f['arrive']}, Saver "
                             f"{money(p['Saver'])}, Flex {money(p['Flex'])}, Business "
                             f"{money(p['Business'])}")
        if self.selected_flight:
            f = self.flights[self.selected_flight]
            lines.append(f"Selected flight: {self.selected_flight} ({f['origin']}->"
                         f"{f['destination']} {f['date']} {f['depart']}).")
        if self.selected_booking and self.verified and \
                self.bookings[self.selected_booking]["customer_id"] == self.verified:
            lines.append(f"Selected booking: {self._brief(self.selected_booking)}.")
        if self.pending:
            lines.append(f"Pending (waiting for the customer's yes): {self.pending['tool']} - "
                         f"{self.pending['summary']}")
        return "\n".join(lines)

    def state_view(self) -> dict[str, Any]:
        return {"today": self.today.isoformat(), "verified": self.verified and
                self.customers[self.verified]["name"],
                "last_search": list(self.last_search), "last_query": dict(self.last_query),
                "selected_flight": self.selected_flight,
                "selected_booking": self.selected_booking,
                "pending": self.pending and {"tool": self.pending["tool"],
                                             "summary": self.pending["summary"]},
                "allowed_tools": sorted(self.allowed_tools())}

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
            if b["status"] == "flown":
                continue
            r = W.FARE_RULES[b["fare"]]
            fee = r["change_fee_intl" if self._intl(b) else "change_fee"]
            fl = "; ".join(f"{v['flight_id']} {v['origin']}->{v['destination']} {v['date']} "
                           f"{v['depart']}-{v['arrive']} seat {v['seat']}"
                           + ("" if v["status"] == "scheduled" else f" ({v['status']})")
                           for v in (self._flight_view(x, code) for x in b["flights"]))
            lines.append(
                f"- {code}: {b['status']}, {b['fare']} fare (change fee {money(fee)} per "
                f"passenger, {'refundable' if r['refundable'] else 'not refundable'}), "
                f"{len(b['passengers'])} passenger(s), {b['bags']} extra bag(s), paid "
                f"{money(b['paid'])}" + (", refunded" if b.get("refunded") else "")
                + (f", promo {b['promo']}" if b.get("promo") else "") + f". Flights: {fl}.")
        lines.append(f"Extra 15 kg bag: {money(W.BAG_PRICE['domestic'])} domestic, "
                     f"{money(W.BAG_PRICE['international'])} international, per passenger.")
        return "\n".join(lines)

    def call(self, tool: str, args: dict[str, Any]) -> dict:
        fn = getattr(self, tool, None)
        if tool.startswith("_") or tool not in TOOL_NAMES or not callable(fn):
            return {"error": "unknown_tool"}
        try:
            return fn(**args)
        except TypeError as e:
            return {"error": "bad_arguments", "message": str(e)[:200]}


TOOL_NAMES = {"find_customer", "verify_customer", "get_customer_details", "get_booking",
              "search_flights", "change_flight", "cancel_booking", "add_baggage",
              "issue_refund", "book_flight", "list_offers", "apply_promo", "transfer_to_human"}


def personas(n: int = 6) -> list[dict]:
    """Persona cards for the presenter: the fixed persona customers of the world (valid emails
    and booking codes) with suggested requests, plus the ambiguous name."""
    s = Session()
    out = []
    for name, email, _tier, bks in W.PERSONAS:
        if sum(1 for p in W.PERSONAS if p[0] == name) > 1:
            continue
        code = bks[0][0]
        b = s.bookings[code]
        f = s.flights[b["flights"][0]]
        o, d = W.AIRPORTS[f["origin"]][0], W.AIRPORTS[f["destination"]][0]
        out.append({"name": name, "email": email, "booking_code": code, "fare": b["fare"],
                    "requests": [f"Hi, I'm {name} ({email}). Can I move my {o} to {d} flight "
                                 f"on {say_date(f['date'])} to a later date?",
                                 f"I'm {name}, booking {code}. Please add one checked bag."]})
        if len(out) >= n:
            break
    amb = next(p[0] for p in W.PERSONAS if sum(1 for q in W.PERSONAS if q[0] == p[0]) > 1)
    out.append({"name": amb, "note": "AMBIGUOUS on purpose: two customers share this name; "
                "Skye must ask for an email or booking code", "requests": [
                    f"Hi, this is {amb}. I need help with my booking."]})
    return out
