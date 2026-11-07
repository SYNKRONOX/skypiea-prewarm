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

SYSTEM = """You are Skye, the customer-support agent of Skypiea Air (a fictional Indian \
airline). Be warm, short and precise. You can: book a flight, look up a booking, change a \
flight's date or time, add checked bags, cancel and refund, and apply offers. Rules:
1. Ask for the customer's full name and email if you do not have them. Existing customers: \
call find_customer with the name AND the email or booking code they gave (that verifies them \
in one call). If the email is not found, ask them to check it; if they are new, offer to \
create a profile by booking (search_flights, then book_flight with their name and email).
2. Places: pass the city or airport as the customer said it (e.g. "Bangalore"); dates: pass \
them as said ("next Friday", "20 Jan"). The tools resolve both against the demo-world date in \
the STATE below; never use another date.
3. Use tools for every fact. Quote prices, fees and refunds (INR) ONLY from tool results or \
the STATE / CUSTOMER SIDEBAR; never compute your own. Never invent a booking code, flight, \
price or result. To book or change, use a flight_id from the last search (in the STATE).
4. Before any booking, change, cancellation, bag, refund or promo: call the tool WITHOUT \
customer_confirmed; it returns a summary (route, date, price). Tell the customer that summary \
and ask "Shall I go ahead?". Only after the customer says yes, call the same tool with \
customer_confirmed=true. If the customer already said yes to exactly that, go ahead.
5. Follow the tool messages: they say what to do next. If a tool says the customer has \
several bookings, ask which one.
6. You only help with Skypiea Air bookings. For anything else (weather, visas, hotels, other \
airlines, coding...) say politely you can only help with Skypiea Air bookings and list what \
you can do. Complaints, lost items, damaged bags and compensation: offer a transfer to a human \
and call transfer_to_human when the customer agrees or asks for a person.
7. When done, summarise in one or two sentences. A goodbye needs no transfer.
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
                ("search_flights", {"origin": "Bengaluru", "destination": "Delhi"})]

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
        fn("search_flights", "Flights between two places (city, alias or airport code, as the "
           "customer said it) on a date (as the customer said it: '20 Jan', 'next Friday'). "
           "Returns the top 5 with flight_id and fares in INR.",
           {"origin": {"type": "string"}, "destination": {"type": "string"},
            "date": {"type": "string"}}, ["origin", "destination"]),
        fn("change_flight", "Move a booking to another flight on the same route: give the "
           "new flight_id (from search_flights), or only new_date to get the options.",
           {"booking_code": _code, "old_flight_no": {"type": "string"},
            "new_flight_no": {"type": "string", "description": "flight_id, e.g. SKY214-20JAN"},
            "new_date": {"type": "string"}, **_conf}),
        fn("cancel_booking", "Cancel a booking.", {"booking_code": _code, **_conf}),
        fn("add_baggage", "Add 1-3 checked bags to a booking.",
           {"booking_code": _code, "bags": {"type": "integer"}, **_conf}, ["bags"]),
        fn("issue_refund", "Refund a cancelled, refund-eligible booking.",
           {"booking_code": _code, **_conf}),
        fn("book_flight", "Book a new trip on one flight: for the verified customer, or for a "
           "NEW customer (full name and an email address the airline does not know yet). "
           "Fares: Saver, Flex, Business.",
           {"flight_no": {"type": "string", "description": "flight_id from search_flights, "
                          "e.g. SKY214-20JAN"}, "fare": {"type": "string"},
            "name": {"type": "string"}, "email": {"type": "string"},
            "passengers": {"type": "integer"},
            "promo_code": {"type": "string", "description": "optional offer code to apply "
                           "to this booking"}, **_conf}, ["flight_no"]),
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
    """The first scheduled, bookable flight on the same route on a later DATE than the
    booking's first flight (from the world, not typed in)."""
    from agent.skyair.tools import pristine

    db = pristine()
    b = next(x for x in db["bookings"] if x["booking_code"] == booking_code)
    f = db["flight_index"][b["flights"][0]]
    return min((x for x in db["flights"] if x["origin"] == f["origin"]
                and x["destination"] == f["destination"] and x["status"] == "scheduled"
                and x["seats_left"] > 0 and x["date"] > f["date"]),
               key=lambda x: (x["date"], x["depart"]))


def _say(iso: str) -> str:
    from agent.skyair.tools import say_date
    return say_date(iso)


_LATER = _later_flight()
SCRIPT = ["Hi, I'd like to move my flight to a later date.",
          "My name is Tara Patel.",
          "My email is tara.patel@example.com and my booking code is 5TGTBP.",
          f"Please move me to the {_LATER['depart']} flight on {_say(_LATER['date'])}. Yes, I "
          "confirm the change and the fee.",
          "Thanks, that's all."]


# Verification scenarios for the autopilot (customer side only; the LLM stays live).
# Fictional persona customers of the synthetic world (agent/skyair/world.py PERSONAS).
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
    "new_customer": [
        "Hi! I've never flown with Skypiea Air before. My name is Nova Quill.",
        "I'd like to fly from Bangalore to Goa next Friday.",
        "The morning one please, Saver fare. My email is nova.quill@example.org.",
        "Yes, I confirm the booking.",
        "Great. Can you show me my booking?",
        "Thanks, that's all."],
    "offers": [
        "Hi, I'm Dev Singh, email dev.singh@example.com.",
        "Do you have any offers at the moment?",
        "Please apply SKYWINTER10 to my booking. Yes, I confirm.",
        "Thanks, that's all."],
    "out_of_scope": [
        "Hi Skye, what will the weather be like in Goa tomorrow?",
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
        return bool(b) and _LATER["flight_id"] in b["flights"]
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
