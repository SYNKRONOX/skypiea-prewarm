"""Guardrails for Skye (W1 night, STEP 3): argument validation before a tool runs, and the
explicit-confirmation rule for writes. Pure functions; the engine applies them and records
every intervention (shown in the Details drawer, kept in the call records).

- validate(tool, args, backend): structural problems only (missing required arguments, wrong
  types, an unknown fare, a malformed email, booking code or flight id). A place or date the
  world does not know is NOT invalid: the tool itself answers with a steering message.
- confirmed_ok(tool, last_user_text, pending): a write with customer_confirmed=true runs only
  if the customer's latest message says yes AND either the tool quoted this action before
  (pending) or the message names the action ("yes, cancel it", "I confirm the booking").
  Otherwise the engine runs it as a quote (nothing written) so the summary is shown first.
"""

from __future__ import annotations

import re
from typing import Any

REQUIRED = {"find_customer": ["name"], "search_flights": ["origin", "destination"],
            "book_flight": ["flight_no"], "apply_promo": ["promo_code"],
            "add_baggage": ["bags"]}
INTS = {"add_baggage": ["bags"], "book_flight": ["passengers"]}
FARES = ("saver", "flex", "business")
_YES = re.compile(r"\b(yes|yeah|yep|yup|confirm\w*|confrim\w*|go ahead|please do|proceed|"
                  r"sure|ok|okay|do it|book it|that'?s fine|sounds good|correct)\b", re.I)
_NO = re.compile(r"\b(no|not yet|don'?t|do not|wait|hold on|cancel that)\b", re.I)
KEYWORDS = {"book_flight": r"\bbook", "change_flight": r"\b(chang|chnag|mov|switch|resched)",
            "cancel_booking": r"\bcancel", "add_baggage": r"\bbag",
            "issue_refund": r"\brefund", "apply_promo": r"\b(promo|code|apply|offer|discount)"}


def validate(tool: str, args: dict[str, Any], backend: Any = None) -> list[str]:
    # small models send the STRING "None" / "null" for optional arguments (seen live with
    # llama3.1:8b): that means "not given", not an invalid value
    for k in [k for k, v in args.items() if isinstance(v, str)
              and v.strip().lower() in ("none", "null", "n/a", "")]:
        del args[k]
    out = []
    for k in REQUIRED.get(tool, []):
        if args.get(k) in (None, ""):
            out.append(f"missing {k}")
    for k in INTS.get(tool, []):
        v = args.get(k)
        if v is not None and not (isinstance(v, int) and not isinstance(v, bool)):
            try:
                args[k] = int(str(v).strip())
            except ValueError:
                out.append(f"{k} must be a whole number (got {v!r})")
    if "customer_confirmed" in args and not isinstance(args["customer_confirmed"], bool):
        v = str(args["customer_confirmed"]).strip().lower()
        if v in ("true", "yes", "1"):
            args["customer_confirmed"] = True
        elif v in ("false", "no", "0", ""):
            args["customer_confirmed"] = False
        else:
            out.append("customer_confirmed must be true or false")
    if tool == "book_flight" and args.get("fare") and str(args["fare"]).lower() not in FARES:
        out.append("fare must be Saver, Flex or Business")
    if args.get("email") and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}",
                                              str(args["email"]).strip()):
        out.append(f"email {args['email']!r} is not a valid address: ask the customer for it")
    code = args.get("booking_code")
    if code and not re.fullmatch(r"[A-Za-z0-9]{6}", str(code).strip()):
        out.append(f"booking_code {code!r} is not a 6-character code")
    if backend is not None and hasattr(backend, "resolve_flight"):
        for k in ("flight_no", "new_flight_no"):
            if args.get(k) and backend.resolve_flight(args[k], args.get("date")
                                                      or args.get("new_date")) is None:
                out.append(f"{k} {args[k]!r} is not a flight_id from the last search")
    return out


def hint(tool: str, problems: list[str], backend: Any = None) -> str:
    h = f"The call to {tool} was not run: " + "; ".join(problems) + ". "
    if any("flight" in p for p in problems) and backend is not None and backend.last_search:
        h += "Use one of these flight_ids: " + ", ".join(backend.last_search) + ". "
    return h + "Fix the arguments and call it again (one retry), or ask the customer."


def affirmative(text: str) -> bool:
    t = text or ""
    return bool(_YES.search(t)) and not (_NO.search(t) and not re.search(r"\byes\b", t, re.I))


def confirmed_ok(tool: str, last_user: str, pending: dict | None) -> bool:
    if not affirmative(last_user):
        return False
    if pending and pending.get("tool") == tool:
        return True
    return bool(re.search(KEYWORDS.get(tool, r"$^"), last_user or "", re.I))
