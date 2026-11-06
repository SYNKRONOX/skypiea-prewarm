"""Story mode (W1 #16): prepared customer/developer lines for a live talk. The lines are sent
to the LIVE model one by one through the autopilot (nothing is pre-recorded; the replies, tool
calls and timings are whatever happens). Act 3 kills the predictor after its first turn
(mid-conversation) and restarts it when the act ends.
"""

from __future__ import annotations

from agent.tasks.skye import _LATER

STORIES: dict[str, list[dict]] = {
    "skyair": [
        {"act": 1, "title": "Act 1: a new customer books a trip",
         "script": ["Hi! I've never flown with Skypiea Air before. My name is Nova Quill.",
                    "I'd like to fly from SKA to SKE on 2026-12-07.",
                    "The 06:15 one please, Saver fare. My email is nova.quill@example.org.",
                    "Yes, I confirm the booking.",
                    "Can I add one checked bag to it? Yes, I confirm.",
                    "Thanks, that's all."]},
        {"act": 2, "title": "Act 2: change the schedule (watch the truth check)",
         "script": ["Hi, I'm Tara Patel. My email is tara.patel@example.com.",
                    "I need to move my flight to a later one on the same route. What is there?",
                    f"Please move me to flight {_LATER['flight_no']} on {_LATER['date']} at "
                    f"{_LATER['depart']}. Yes, I confirm the change and the fee.",
                    "Thanks, that's all."]},
        {"act": 3, "title": "Act 3: kill the predictor mid-conversation",
         "kill_after_turn": 1,
         "script": ["Hi, I'm Dev Singh. My email is dev.singh@example.com.",
                    "Please add one checked bag to my booking. Yes, I confirm.",
                    "Do you have any offers at the moment?",
                    "Thanks, that's all."]},
    ],
    "codefix": [
        {"act": 1, "title": "Act: fix the failing merge_sort test",
         "script": ["The merge_sort tests are failing. Please find the bug in sortkit, fix it "
                    "and run the tests.",
                    "Thanks. Which files did you change, and what do the tests say now?",
                    "And what's the weather like today?"]},
    ],
}


def stories(demo: str) -> list[dict]:
    return STORIES.get(demo, [])
