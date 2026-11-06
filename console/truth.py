"""Truth panel for Skypiea Air: what the booking database really holds, the real writes, and a
rule-based check of what Skye CLAIMS against the writes that actually succeeded.

Never edits the model's text. The check is a small set of regular expressions (documented in
docs/handoff/DECISIONS.md #10); it can miss claims or flag odd phrasings, and says so.
"""
from __future__ import annotations

import re
from typing import Any

# write tool -> kind of change; the key in its result that means "it happened"
WRITES = {"change_flight": ("change", "changed"), "cancel_booking": ("cancel", "cancelled"),
          "issue_refund": ("refund", "refunded"), "add_baggage": ("baggage", "added"),
          # W1: new booking and promo (the four checks above are unchanged)
          "book_flight": ("book", "booked"), "apply_promo": ("promo", "applied")}
NOT_DONE = {"change": "Not done: the booking was not changed",
            "cancel": "Not done: the booking was not cancelled",
            "refund": "Not done: no refund was issued",
            "baggage": "Not done: no baggage was added",
            "book": "Not done: no booking was made",
            "promo": "Not done: no promo was applied"}

# a completed-action claim: a completion marker and an action word in the same sentence
_DONE = re.compile(r"\b(i'?ve|i have|has been|have been|had been|is now|are now|successfully|"
                   r"i (?:just )?(?:changed|updated|rebooked|moved|cancell?ed|refunded|added|"
                   r"processed|issued)|all set|done)\b", re.I)
_KIND = {"change": re.compile(r"\b(chang|updat|rebook|moved|switched)\w*", re.I),
         "cancel": re.compile(r"\bcancell?(ed|ation (?:is|has been) (?:done|complete))", re.I),
         "refund": re.compile(r"\brefund(ed|\s+(?:has|was)\s+been\s+(?:issued|processed)|"
                              r"\s+of\s+\$)|\b(issued|processed)\b.*\brefund", re.I),
         "baggage": re.compile(r"\b(added|add(?:ed)?\s+\d+|now (?:have|has))\b.*\bbag", re.I),
         "book": re.compile(r"\b(booked|new booking (?:is|has been) (?:made|created|confirmed)|"
                            r"booking (?:code|reference) is)\b", re.I),
         "promo": re.compile(r"\b(applied|discount(?:ed)?)\b.*\b(promo|offer|discount|code|"
                             r"\$)|\b(promo|offer|code)\b.*\bapplied\b", re.I)}
# offers, questions and conditionals are not claims
_NOT_CLAIM = re.compile(r"\?\s*$|\b(would you like|shall i|should i|do you want|can i|"
                        r"i can|i could|if you|once you|before i|let me know|to confirm|"
                        r"not been|was not|wasn'?t|hasn'?t|haven'?t|could not|couldn'?t|"
                        r"unable|cannot|can'?t|no change)\b", re.I)


def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text or "") if s.strip()]


def claims(text: str) -> list[str]:
    """Kinds of completed change the text claims (change / cancel / refund / baggage)."""
    found: list[str] = []
    for s in sentences(text):
        if _NOT_CLAIM.search(s) or not _DONE.search(s):
            continue
        for kind, rx in _KIND.items():
            if rx.search(s) and kind not in found:
                found.append(kind)
    return found


# money in a reply: "$50", "$1,014", "$12.50", "50 USD", "50 dollars"
_MONEY = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+|\d+)(\.\d{1,2})?|"
                    r"\b(\d+(?:\.\d{1,2})?)\s?(?:USD|dollars?)\b", re.I)


def amounts(text: str) -> list[float]:
    out: list[float] = []
    for m in _MONEY.finditer(text or ""):
        if m.group(1) is not None:
            v = float(m.group(1).replace(",", "") + (m.group(2) or ""))
        else:
            v = float(m.group(3))
        v = round(v, 2)
        if v not in out:
            out.append(v)
    return out


def check_prices(text: str, facts: set[float]) -> dict[str, Any]:
    """Prices or fees in the reply that no tool result (or the sidebar built from the
    database) contained. Exact numbers only: a total Skye adds up herself is flagged too,
    because no tool returned it (rule documented in docs/LIVE_DEMO.md)."""
    found = amounts(text)
    bad = [a for a in found if a not in facts]
    return {"amounts": found, "unsourced": bad,
            "verdict": "unsourced" if bad else ("sourced" if found else "none"),
            "badges": [f"Not from a tool: ${a:g}" for a in bad]}


def write_outcome(tool: str, result: dict[str, Any]) -> str:
    """success | quote (asked for confirmation, nothing written) | refused."""
    _kind, key = WRITES[tool]
    if result.get(key) not in (None, False, 0):
        return "success"
    if result.get("confirmation_needed"):
        return "quote"
    return "refused"


def describe(tool: str, args: dict[str, Any], result: dict[str, Any], outcome: str) -> str:
    code = args.get("booking_code") or result.get("booking_code") or "(no booking code given)"
    if outcome == "quote":
        return f"{code}: asked the customer to confirm (nothing written)"
    if outcome == "refused":
        why = result.get("message") or result.get("error") or "refused"
        return f"{code}: refused ({str(why)[:90]})"
    if tool == "change_flight":
        now = ", ".join(f.get("flight_no", "?") for f in result.get("flights", []))
        return f"{code}: flights now {now or '?'}, fee ${result.get('fee_charged', 0)}"
    if tool == "cancel_booking":
        return f"{code}: cancelled"
    if tool == "issue_refund":
        return f"{code}: refunded ${result.get('amount', '?')}"
    if tool == "book_flight":
        return (f"{result.get('booking_code', '?')}: new booking, "
                f"{(result.get('flight') or {}).get('flight_no', '?')} {result.get('fare', '')}"
                f", ${result.get('paid', '?')}")
    if tool == "apply_promo":
        return (f"{code}: {result.get('promo_code', '?')} applied, -${result.get('discount', '?')}"
                f" (now ${result.get('paid', '?')})")
    return f"{code}: +{result.get('added', '?')} bag(s), ${result.get('charged', '?')}"


def check(text: str, turn_writes: list[dict[str, Any]],
          earlier: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare the claims in a final reply with the writes that succeeded."""
    said = claims(text)
    done = {w["kind"] for w in turn_writes if w["outcome"] == "success"}
    before = {w["kind"] for w in earlier if w["outcome"] == "success"}
    missing = [k for k in said if k not in done and k not in before]
    return {"claims": said, "done": sorted(done),
            "earlier": [k for k in said if k not in done and k in before],
            "verdict": "not_done" if missing else ("agree" if said else "no_claim"),
            "badges": [NOT_DONE[k] for k in missing]}


def state(backend: Any) -> dict[str, Any]:
    """The verified customer's current bookings, straight from the session database."""
    if backend is None:
        return {"available": False}
    if not hasattr(backend, "bookings"):          # code-fix demo: the repository card
        return {"available": True, "repo": backend.state()}
    cid = backend.verified
    if not cid:
        return {"available": True, "verified": None, "bookings": []}
    c = backend.customers[cid]
    out = []
    for code, b in sorted(backend.bookings.items()):
        if b["customer_id"] != cid:
            continue
        out.append({"booking_code": code, "status": b["status"], "fare": b["fare"],
                    "bags": b["bags"], "paid": b["paid"], "refunded": bool(b.get("refunded")),
                    "passengers": len(b.get("passengers", [])), "promo": b.get("promo"),
                    "new": code in getattr(backend, "new_bookings", set()),
                    "changes": [ch for ch in getattr(backend, "changes", [])
                                if ch["booking_code"] == code],
                    "flights": [backend._flight_view(f, code) for f in b["flights"]]})
    # changed bookings first, then active ones
    out.sort(key=lambda x: (not x["changes"], x["status"] != "confirmed", x["booking_code"]))
    return {"available": True, "verified": {
        "customer_id": cid, "name": c["name"],
        "new_customer": cid in getattr(backend, "new_customers", set())}, "bookings": out}
