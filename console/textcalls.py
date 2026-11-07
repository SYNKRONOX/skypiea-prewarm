"""Tool calls written as TEXT in an assistant message (fix/free-typing).

llama3.1:8b via Ollama sometimes writes a tool call as JSON in its message content instead of
sending it as a tool call, also for names that are not tools at all: a customer typing "hello"
got the raw text {"name": "hello", "parameters": {}} as Skye's reply. This module finds such
objects (plain, or in a ``` / ```json fence, optionally after a <|python_tag|>), so the engine
can run a real, offered tool or else hide the text, log it and retry.
"""

from __future__ import annotations

import json
import re
from typing import Any

# what must never reach the customer: an object with "name" and "parameters"/"arguments"
TOOLCALL_RX = re.compile(r'\{\s*"name"\s*:\s*"[^"]*"\s*,\s*"(?:parameters|arguments)"\s*:'
                         r'|\{\s*"(?:parameters|arguments)"\s*:.*?"name"\s*:', re.S)
_FENCE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.S)
_TAGS = re.compile(r"<\|python_tag\|>|<\|eom_id\|>|<\|eot_id\|>")


def looks_like_call(text: str) -> bool:
    return bool(TOOLCALL_RX.search(text or ""))


def might_start_call(prefix: str) -> bool:
    """While streaming: could the reply so far be (the start of) a JSON tool call? Hold the
    text back from the customer until the reply is complete."""
    t = _TAGS.sub("", prefix or "").lstrip()
    return t.startswith(("{", "`", "[")) or looks_like_call(t) or '{"name"' in t \
        or "{\n" in t


def find_calls(content: str) -> tuple[list[tuple[str, dict[str, Any]]], str]:
    """(calls, remaining text). calls = [(name, args)] for every JSON object with a "name" and
    "parameters" or "arguments"; the remaining text has them (and empty fences) removed."""
    text = _TAGS.sub("", content or "")
    calls: list[tuple[str, dict[str, Any]]] = []
    spans: list[tuple[int, int]] = []
    dec = json.JSONDecoder()
    i = text.find("{")
    while i != -1:
        try:
            obj, end = dec.raw_decode(text, i)
        except ValueError:
            i = text.find("{", i + 1)
            continue
        if isinstance(obj, dict) and isinstance(obj.get("name"), str) and (
                "parameters" in obj or "arguments" in obj):
            args = obj.get("parameters", obj.get("arguments"))
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {}
            calls.append((obj["name"], args if isinstance(args, dict) else {}))
            spans.append((i, end))
        i = text.find("{", end)
    rest = text
    for a, b in reversed(spans):
        rest = rest[:a] + rest[b:]
    rest = _FENCE.sub(lambda m: m.group(0) if m.group(1).strip() else "", rest)
    rest = re.sub(r"[ \t]+\n", "\n", rest).strip()
    if looks_like_call(rest):            # a malformed object that did not parse
        rest = TOOLCALL_RX.split(rest)[0].strip()
    return calls, rest


FALLBACK = ("Sorry, I didn't quite catch that. I'm Skye from Skypiea Air: I can book a flight, "
            "change or cancel a booking, add bags, process refunds and apply offers. What would "
            "you like to do?")
EMPTY_REPLY = ("It looks like your message was empty. How can I help you today? I can book a "
               "flight, change or cancel a booking, add bags, process refunds and apply offers.")
RETRY_HINT = ("Your last reply was not shown to the customer: it was written as a tool call in "
              "text. Answer the customer in plain language; only call the listed tools, through "
              "the tool-calling interface.")
