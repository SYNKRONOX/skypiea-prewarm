"""Issue #19: a write quote expires when the customer declines it, moves on to another request,
or does not answer it on the next turn; a later unrelated "yes" cannot execute it."""

from __future__ import annotations

import asyncio

from console import guards
from tests.test_free_typing import _conv

Q = {"tool": "cancel_booking", "args": {"booking_code": "VPGX3T"}, "summary": "cancel"}


def test_rules():
    assert guards.quote_expired(dict(Q, turn=1), "yes", 2) is None
    assert guards.quote_expired(dict(Q, turn=1), "no, don't cancel it", 2) == "declined"
    assert guards.quote_expired(dict(Q, turn=1), "how many bags can I take?", 2) == \
        "new request"
    assert guards.quote_expired(dict(Q, turn=1), "yes", 3) == "stale"


def _with_pending(answers):
    conv, _ = _conv(answers)
    conv.backend.pending = dict(Q)
    return conv


def test_declined_quote_is_cleared_by_the_engine():
    conv = _with_pending(["Okay, I won't cancel it."])
    asyncio.run(conv.user_turn("no, don't cancel it, leave it"))
    assert conv.backend.pending is None
    assert any(g["kind"] == "quote_expired" for g in conv.guards)


def test_yes_on_the_next_turn_keeps_the_quote():
    conv = _with_pending(["Done."])
    conv.turn = 3
    conv.backend.pending = dict(Q, turn=3)
    asyncio.run(conv.user_turn("yes"))
    assert conv.backend.pending is not None
    assert not any(g["kind"] == "quote_expired" for g in conv.guards)
