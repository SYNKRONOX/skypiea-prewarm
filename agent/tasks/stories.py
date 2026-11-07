"""Scenarios (shown as "Scenario: ..." with Auto-play; the code and API still call them
story acts: route /api/story, key "act"). Prepared, SCRIPTED customer/developer lines for a
live talk, sent to the LIVE model one by one through the autopilot (nothing is pre-recorded;
the replies, tool calls and timings are whatever happens). Scenario 3 (cancel and refund)
kills the predictor after its first turn (mid-conversation) and restarts it when it ends.
"""

from __future__ import annotations

from agent.tasks.skye import _LATER, _say

STORIES: dict[str, list[dict]] = {
    "skyair": [
        {"act": 1, "title": "New customer books a trip",
         "script": ["Hi! I've never flown with Skypiea Air before. My name is Nova Quill.",
                    "I'd like to fly from Bangalore to Goa next Friday.",
                    "The morning one please, Saver fare. My email is nova.quill@example.org.",
                    "Yes, I confirm the booking.",
                    "Can I add one checked bag to it? Yes, I confirm.",
                    "Thanks, that's all."]},
        # Tara Patel has TWO active bookings (5TGTBP, 8RK2RN): the customer names one
        {"act": 2, "title": "Change a booking",
         "script": ["Hi, I'm Tara Patel. My email is tara.patel@example.com.",
                    "I need to move my booking 5TGTBP to a later date on the same route. "
                    "What is there?",
                    f"Please move me to the {_LATER['depart']} flight on {_say(_LATER['date'])}. "
                    "Yes, I confirm the change and the fee.",
                    "Thanks, that's all."]},
        # Asha Reddy's S4Z64E is a refundable fare: cancel, then refund (predictor killed
        # after turn 1: every call still succeeds, without prediction)
        {"act": 3, "title": "Cancel and refund",
         "kill_after_turn": 1,
         "script": ["Hi, I need to cancel a trip. I'm Asha Reddy, email asha.reddy@example.com.",
                    "Please cancel booking S4Z64E. Yes, I confirm the cancellation.",
                    "And please refund it to my card. Yes, I confirm the refund.",
                    "Thanks, that's all."]},
    ],
    "codefix": [
        {"act": 1, "title": "Fix the failing merge_sort test",
         "script": ["The merge_sort tests are failing. Please find the bug in sortkit, fix it "
                    "and run the tests.",
                    "Thanks. Which files did you change, and what do the tests say now?",
                    "And what's the weather like today?"]},
    ],
}


def stories(demo: str) -> list[dict]:
    return STORIES.get(demo, [])
