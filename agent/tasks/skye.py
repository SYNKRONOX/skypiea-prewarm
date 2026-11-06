"""Skye, the support agent of Skypiea Air (fictional airline), for the Live Console.

Tool NAMES are ours; each maps to a class the real `support` profile's vocabulary knows (see
docs/handoff/DECISIONS.md #2), so prediction works. The tool pods are called through both
gateways for timing only, WITHOUT customer data (empty arguments); the answer Skye sees comes
from the in-console Skypiea Air session (agent/skyair/tools.py), where writes persist for the
conversation and a reset restores the database.
"""

from __future__ import annotations

from typing import Any

from agent.tasks import Task, fn

CLASS = {"find_customer": "user_lookup", "verify_customer": "user_lookup",
         "get_customer_details": "user_lookup", "get_booking": "reservation_lookup",
         "search_flights": "flight_search", "change_flight": "reservation_modify",
         "add_baggage": "reservation_modify", "cancel_booking": "reservation_cancel",
         "issue_refund": "reservation_cancel", "transfer_to_human": "escalate",
         # W1: booking a new trip is a class the support vocabulary knows; offers are not
         # (no promo class in tau2-bench): the predictor abstains, shown as "unknown tool for
         # the predictor: no prediction, normal behaviour" (DECISIONS W1-6)
         "book_flight": "reservation_book", "list_offers": "promo_code_lookup",
         "apply_promo": "promo_code_lookup"}

SYSTEM = """You are Skye, the customer-support agent of Skypiea Air (a fictional airline). \
Be warm, short and precise. Rules:
1. Greet the customer and ask for their full name if you do not have it.
2. Look the name up with find_customer. If several customers match, ask for their email or \
booking code. If none match, ask them to check the spelling; if they are new to Skypiea Air, \
welcome them as a new customer, ask where they fly from, where to and on which date, call \
search_flights, and offer to book with book_flight (their full name and email address).
3. Before sharing or changing anything for an existing customer, verify identity with \
verify_customer using their email address or a booking code; if the customer already gave \
one (even in the same message as their name), call verify_customer with it right away instead \
of asking again. If verification fails, politely \
refuse and offer a hand-over to a human. Never assume or invent an identity, booking code, \
flight, price or result.
4. Use tools for every fact. Quote prices, fees, refunds and discounts ONLY from tool results \
or the customer sidebar; never estimate or compute your own. Airports are codes like SKA, SKB.
5. Before any change (book_flight, change_flight, cancel_booking, add_baggage, issue_refund, \
apply_promo) state what will happen and the cost, ask the customer to confirm, and only then \
call the tool with customer_confirmed=true. If the customer has one active booking you may \
leave booking_code out; if a tool says several bookings match, ask which one.
6. Offers: list_offers shows the current offers; apply_promo applies one to a booking.
7. You only help with Skypiea Air bookings: finding and verifying customers, booking, \
checking, changing or cancelling flights, baggage, refunds and offers. For anything else \
(weather, visas, other airlines, general knowledge, coding...) say politely that you can only \
help with Skypiea Air bookings and list what you can do; do not answer it.
8. When done, summarise what you did in one or two sentences. Transfer to a human only if \
the customer asks for one or you cannot help; a goodbye needs no transfer.
Call tools through the tool-calling interface; never write a tool call as text."""


# Argument synonyms small models use (seen live: new_flight_id); mapped before the schema filter
ALIASES = {"new_flight_id": "new_flight_no", "new_flight": "new_flight_no",
           "flight_number": "new_flight_no", "new_flight_number": "new_flight_no",
           "old_flight_id": "old_flight_no", "booking_id": "booking_code",
           "booking": "booking_code", "reservation_code": "booking_code",
           "promo": "promo_code", "code": "promo_code", "coupon": "promo_code",
           "voucher": "promo_code", "fare_class": "fare", "flight_id": "flight_no",
           "flight": "flight_no", "flight_number_to_book": "flight_no",
           "reservation_id": "booking_code", "email_address": "email",
           "customer_name": "name", "full_name": "name", "confirmed": "customer_confirmed",
           "num_passengers": "passengers", "number_of_passengers": "passengers",
           "number_of_bags": "bags"}


class Skye(Task):
    def normalize_args(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        allowed = self.params(tool)
        out = {}
        for k, v in args.items():
            k2 = ALIASES.get(k, k)
            if k2 in allowed and k2 not in args:
                out[k2] = v
            else:
                out[k] = v
        return out

    def user_prompt(self, i: int) -> str:
        return "Hi, I need help with my booking."

    def scripted(self, i: int) -> list[tuple[str, dict[str, Any]]]:
        return [("find_customer", {"name": "Tara Patel"}),
                ("verify_customer", {"name": "Tara Patel", "email": "tara.patel@example.com"}),
                ("get_booking", {"booking_code": "5TGTBP"}),
                ("search_flights", {"origin": "SKB", "destination": "SKE"})]

    def gateway_call(self, tool: str, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        # timing only: no customer data leaves the console
        return CLASS.get(tool, "other"), {}


_conf = {"customer_confirmed": {"type": "boolean",
                                "description": "true only after the customer said yes"}}
_code = {"type": "string", "description": "the booking code; may be left out if the verified "
         "customer has exactly one active booking"}
SKYE = Skye(
    scenario="skyair", name="skypiea_air_support", system=SYSTEM, profile="support",
    tools=[
        fn("find_customer", "Find a customer by full name. If the customer gave their email "
           "or a booking code, pass it too: a single match is then verified in the same call.",
           {"name": {"type": "string"}, "email": {"type": "string"},
            "booking_code": {"type": "string"}}, ["name"]),
        fn("verify_customer", "Verify identity with an email address or a booking code.",
           {"name": {"type": "string"}, "customer_id": {"type": "string"},
            "email": {"type": "string"}, "booking_code": {"type": "string"}}),
        fn("get_customer_details", "The verified customer's profile and booking codes.",
           {"customer_id": {"type": "string"}}),
        fn("get_booking", "A booking: flights, seats, passengers, fare, bags, fees, refund "
           "eligibility. booking_code may be left out if the customer has one active booking.",
           {"booking_code": _code}),
        fn("search_flights", "Flights between two airports (optionally on a date YYYY-MM-DD).",
           {"origin": {"type": "string"}, "destination": {"type": "string"},
            "date": {"type": "string"}}, ["origin", "destination"]),
        fn("change_flight", "Move a booking from one flight to another on the same route.",
           {"booking_code": _code, "old_flight_no": {"type": "string"},
            "new_flight_no": {"type": "string"}, **_conf}, ["new_flight_no"]),
        fn("cancel_booking", "Cancel a booking.", {"booking_code": _code, **_conf}),
        fn("add_baggage", "Add 1-3 checked bags to a booking.",
           {"booking_code": _code, "bags": {"type": "integer"}, **_conf}, ["bags"]),
        fn("issue_refund", "Refund a cancelled, refund-eligible booking.",
           {"booking_code": _code, **_conf}),
        fn("book_flight", "Book a new trip on one flight: for the verified customer, or for a "
           "NEW customer (full name and an email address the airline does not know yet). "
           "Fares: Saver, Flex, Business.",
           {"flight_no": {"type": "string"}, "fare": {"type": "string"},
            "name": {"type": "string"}, "email": {"type": "string"},
            "passengers": {"type": "integer"}, **_conf}, ["flight_no"]),
        fn("list_offers", "Skypiea Air's current offers (promo codes and conditions).", {}),
        fn("apply_promo", "Apply an offer's promo code to a booking.",
           {"booking_code": _code, "promo_code": {"type": "string"}, **_conf},
           ["promo_code"]),
        fn("transfer_to_human", "Hand the conversation to a human colleague.",
           {"summary": {"type": "string"}}, ["summary"])],
    start="find_customer",
    edges={t: set(CLASS) | {"<end>"} for t in CLASS},
    canonical=["find_customer", "verify_customer", "get_booking", "search_flights",
               "change_flight"])

def _later_flight(booking_code: str = "5TGTBP") -> dict:
    """The first scheduled, bookable flight on the same route after the booking's first
    flight (from the database, not typed in)."""
    from agent.skyair.tools import pristine

    db = pristine()
    b = next(x for x in db["bookings"] if x["booking_code"] == booking_code)
    f = next(x for x in db["flights"] if x["flight_no"] == b["flights"][0])
    return min((x for x in db["flights"] if x["origin"] == f["origin"]
                and x["destination"] == f["destination"] and x["status"] == "scheduled"
                and x["seats_left"] > 0 and (x["date"], x["depart"]) > (f["date"], f["depart"])),
               key=lambda x: (x["date"], x["depart"]))


_LATER = _later_flight()
SCRIPT = ["Hi, I'd like to move my flight to a later one.",
          "My name is Tara Patel.",
          "My email is tara.patel@example.com and my booking code is 5TGTBP.",
          f"Please move me to flight {_LATER['flight_no']} on {_LATER['date']} at "
          f"{_LATER['depart']}. Yes, I confirm the change.",
          "Thanks, that's all."]


# Verification scenarios for the autopilot (customer side only; the LLM stays live).
# Fictional personas from agent/skyair/skyair_db.json.
SCENARIOS: dict[str, list[str]] = {
    "change": SCRIPT,
    "cancel_refund": [
        "Hi, I need to cancel a trip.",
        "I'm Asha Reddy, email asha.reddy@example.com, booking S4Z64E.",
        "Please cancel booking S4Z64E. Yes, I confirm the cancellation.",
        "And please refund it to my card. Yes, I confirm the refund.",
        "Thanks, that's all."],
    "failed_verification": [
        "Hi, I want to change my booking.",
        "My name is Kabir Kapoor.",
        "My email is kabir.k@wrong-example.com and my booking code is ZZZZZZ.",
        "I'm sure that's right. Just change it please.",
        "OK, thanks."],
    "ambiguous_name": [
        "Hi, this is Arjun Das. I need help with my booking.",
        "I don't remember the code. Can you just look me up by name?",
        "Fine, thank you."],
    "unknown_tool": [
        "Hi, I'm Dev Singh, email dev.singh@example.com, booking VPGX3T.",
        "Can you apply my loyalty voucher LOYAL50 and upgrade me to first class using it?",
        "Thanks."],
    # W1 (bake-off personas): a new customer books a trip; offers; out of scope
    "new_customer": [
        "Hi! I've never flown with Skypiea Air before. My name is Nova Quill.",
        "I'd like to fly from SKA to SKE on 2026-12-07.",
        "The 06:15 one please, Saver fare. My email is nova.quill@example.org.",
        "Yes, I confirm the booking.",
        "Great. Can you show me my booking?",
        "Thanks, that's all."],
    "offers": [
        "Hi, I'm Dev Singh, email dev.singh@example.com.",
        "Sure: my email is dev.singh@example.com. Do you have any offers at the moment?",
        "Please apply SKYWINTER10 to my booking. Yes, I confirm.",
        "Thanks, that's all."],
    "out_of_scope": [
        "Hi Skye, what will the weather be like in Paris tomorrow?",
        "OK. Can you write me a Python function that sorts a list?",
        "Fine, thanks."],
}

# The bake-off's five personas (docs/LIVE_DEMO.md "Model bake-off")
BAKEOFF = ["new_customer", "change", "cancel_refund", "offers", "failed_verification"]


def scenario_done(name: str, backend: Any, writes: list[dict[str, Any]],
                  calls: list[dict[str, Any]]) -> bool | None:
    """Did the conversation reach the persona's goal? From the session database and the
    write log only (not from what Skye said). None = no rule for this scenario."""
    ok = {w["tool"] for w in writes if w["outcome"] == "success"}
    if backend is None:
        return None
    if name == "new_customer":
        return any(backend.bookings[c]["customer_id"] in backend.new_customers
                   for c in backend.new_bookings)
    if name == "change":
        b = backend.bookings.get("5TGTBP")
        return bool(b) and _LATER["flight_no"] in b["flights"]
    if name == "cancel_refund":
        b = backend.bookings.get("S4Z64E")
        return bool(b) and b["status"] == "cancelled" and bool(b.get("refunded"))
    if name == "offers":
        b = backend.bookings.get("VPGX3T")
        return bool(b) and b.get("promo") == "SKYWINTER10"
    if name == "failed_verification":
        # the right outcome is a polite refusal: nobody verified, nothing written
        return backend.verified is None and not ok
    if name == "out_of_scope":
        return not ok and not any(c.get("tool") not in ("transfer_to_human",)
                                  for c in calls)
    return None
