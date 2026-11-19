"""Issue #9: "yes, but don't ..." is not consent, and action keywords match whole words only
("booking" is not "book", "booking code" is not a promo)."""

from __future__ import annotations

import pytest

from console import guards


@pytest.mark.parametrize("text", [
    "Yes, but please do not cancel my booking",
    "yes but don't cancel it",
    "Yes. No, wait.",
    "yes, hold on",
    "please do not",
    "yes, but don’t cancel",
    "Yes, never mind",
])
def test_negated_yes_is_not_affirmative(text):
    assert not guards.affirmative(text)
    for tool in ("cancel_booking", "book_flight", "apply_promo", "issue_refund"):
        assert not guards.confirmed_ok(tool, text, {"tool": tool, "args": {}})


@pytest.mark.parametrize("text", ["yes", "Yes please", "yes, go ahead", "ok, do it",
                                  "I confirm", "sure, cancel it"])
def test_plain_yes_still_counts(text):
    assert guards.affirmative(text)


def test_keywords_are_whole_words():
    assert not guards.confirmed_ok("book_flight", "yes, what is my booking status", None)
    assert not guards.confirmed_ok("apply_promo", "yes my booking code is ABC123", None)
    assert guards.confirmed_ok("book_flight", "yes, book it", None)
    assert guards.confirmed_ok("apply_promo", "yes, apply the promo", None)
    assert guards.confirmed_ok("cancel_booking", "yes, cancel it", None)


@pytest.mark.parametrize("text", ["Yes, I confirm the booking.", "yes, go ahead with the booking",
                                  "Yes, please make the booking", "yes, proceed with this booking"])
def test_confirming_the_booking_names_book_flight(text):
    # scenario suite new_booking (llama3.1:8b, fix/redteam-2): the whole-word change made
    # "I confirm the booking" no longer name book_flight, so the new booking was never made
    assert guards.confirmed_ok("book_flight", text, None)


@pytest.mark.parametrize("text", ["yes, cancel my booking", "yes, change my booking",
                                  "yes, what is my booking status"])
def test_other_booking_phrases_do_not_name_book_flight(text):
    assert not guards.confirmed_ok("book_flight", text, None)
