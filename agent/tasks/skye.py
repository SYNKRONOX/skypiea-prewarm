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
         "issue_refund": "reservation_cancel", "transfer_to_human": "escalate"}

SYSTEM = """You are Skye, the customer-support agent of Skypiea Air. Be warm, short and \
precise. Rules:
1. Greet the customer and ask for their full name if you do not have it.
2. Look the name up with find_customer. If several customers match, or none, ask again.
3. Before sharing or changing anything, verify identity with verify_customer using the \
customer's email address or a booking code. If verification fails, politely refuse and offer \
a hand-over to a human. Never assume or invent an identity, booking, flight or result.
4. Use tools for every fact. Airports are codes like SKA, SKB.
5. Before any change (change_flight, cancel_booking, add_baggage, issue_refund) state what \
will happen and the cost, ask the customer to confirm, and only then call the tool with \
customer_confirmed=true.
6. When done, summarise what you did in one or two sentences.
Call tools through the tool-calling interface; never write a tool call as text."""


# Argument synonyms small models use (seen live: new_flight_id); mapped before the schema filter
ALIASES = {"new_flight_id": "new_flight_no", "new_flight": "new_flight_no",
           "flight_number": "new_flight_no", "new_flight_number": "new_flight_no",
           "old_flight_id": "old_flight_no", "booking_id": "booking_code",
           "booking": "booking_code", "reservation_code": "booking_code",
           "reservation_id": "booking_code", "email_address": "email",
           "customer_name": "name", "full_name": "name", "confirmed": "customer_confirmed",
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
SKYE = Skye(
    scenario="skyair", name="skypiea_air_support", system=SYSTEM, profile="support",
    tools=[
        fn("find_customer", "Find a customer by full name.", {"name": {"type": "string"}},
           ["name"]),
        fn("verify_customer", "Verify identity with an email address or a booking code.",
           {"name": {"type": "string"}, "customer_id": {"type": "string"},
            "email": {"type": "string"}, "booking_code": {"type": "string"}}),
        fn("get_customer_details", "The verified customer's profile and booking codes.",
           {"customer_id": {"type": "string"}}),
        fn("get_booking", "A booking: flights, passengers, fare, bags, refund eligibility.",
           {"booking_code": {"type": "string"}}, ["booking_code"]),
        fn("search_flights", "Flights between two airports (optionally on a date YYYY-MM-DD).",
           {"origin": {"type": "string"}, "destination": {"type": "string"},
            "date": {"type": "string"}}, ["origin", "destination"]),
        fn("change_flight", "Move a booking from one flight to another on the same route.",
           {"booking_code": {"type": "string"}, "old_flight_no": {"type": "string"},
            "new_flight_no": {"type": "string"}, **_conf}, ["booking_code", "new_flight_no"]),
        fn("cancel_booking", "Cancel a booking.", {"booking_code": {"type": "string"}, **_conf},
           ["booking_code"]),
        fn("add_baggage", "Add 1-3 checked bags to a booking.",
           {"booking_code": {"type": "string"}, "bags": {"type": "integer"}, **_conf},
           ["booking_code", "bags"]),
        fn("issue_refund", "Refund a cancelled, refund-eligible booking.",
           {"booking_code": {"type": "string"}, **_conf}, ["booking_code"]),
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
}
