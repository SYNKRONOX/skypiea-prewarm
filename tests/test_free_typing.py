"""fix/free-typing: tool calls written as JSON text are never shown to the customer or run
unless the tool is offered; one corrective retry, then a fixed fallback; empty input."""

from __future__ import annotations

import asyncio
import json

import pytest

from console import textcalls

BAD = '{"name": "hello", "parameters": {}}'


@pytest.mark.parametrize("text,names,rest", [
    (BAD, ["hello"], ""),
    ('```json\n{"name": "greet", "arguments": {"x": 1}}\n```', ["greet"], ""),
    ('<|python_tag|>{"name": "search_flights", "parameters": {"origin": "BLR"}}',
     ["search_flights"], ""),
    ('Sure! {"name": "list_offers", "parameters": {}}', ["list_offers"], "Sure!"),
    ("Hello! How can I help you today?", [], "Hello! How can I help you today?"),
])
def test_find_calls(text, names, rest) -> None:
    calls, r = textcalls.find_calls(text)
    assert [n for n, _ in calls] == names and r == rest
    assert textcalls.looks_like_call(text) == bool(names)


def test_might_start_call_holds_json_like_prefixes() -> None:
    assert textcalls.might_start_call('{"na') and textcalls.might_start_call("```")
    assert not textcalls.might_start_call("Hello there")


def _conv(answers, turns=None):
    pytest.importorskip("starlette")
    from agent.suite.run import NullMcp, NullPoller, NullStack
    from agent.tasks import get
    from console.engine import Conversation
    from console.llm_stream import ScriptedStreamingLLM
    events = []

    async def publish(ev):
        events.append(ev)
    llm = ScriptedStreamingLLM(turns or [[] for _ in answers], answers=answers)
    conv = Conversation(get("skyair"), llm, {"base": NullStack("base"),
                                             "pred": NullStack("pred")}, NullPoller(), publish,
                        {"mirror": "concurrent", "hint_mode": "off",
                         "baseline_policy": "scale_to_zero"}, mcp_factory=lambda u: NullMcp())
    return conv, events


def _shown(events) -> str:
    return json.dumps([e for e in events if e["type"] in ("llm_delta", "llm_replace",
                                                          "llm_reply")])


def test_unknown_text_call_is_hidden_retried_then_answered() -> None:
    conv, events = _conv([BAD, "Hello! I'm Skye. How can I help?"])
    asyncio.run(conv.user_turn("hello"))
    assert '"name": "hello"' not in _shown(events) and '\\"name\\"' not in _shown(events)
    assert not conv.calls                                          # nothing executed
    assert [g["kind"] for g in conv.guards] == ["ignored_text_call"]
    said = [m["content"] for m in conv.messages if m["role"] == "assistant"]
    assert said == ["Hello! I'm Skye. How can I help?"]


def test_second_failure_gives_the_fallback_reply() -> None:
    conv, events = _conv([BAD, BAD])
    asyncio.run(conv.user_turn("hello"))
    said = [m["content"] for m in conv.messages if m["role"] == "assistant"]
    assert said == [textcalls.FALLBACK] and not conv.calls
    assert len(conv.guards) == 2 and "name" not in _shown(events).replace(
        textcalls.FALLBACK, "")


def test_offered_tool_written_as_text_runs_as_a_real_call() -> None:
    txt = '{"name": "list_offers", "parameters": {}}'
    conv, _ = _conv([txt, "We have four offers."], turns=[[], []])
    asyncio.run(conv.user_turn("any offers?"))
    assert [c["tool"] for c in conv.calls] == ["list_offers"]
    assert conv.calls[0]["tool_call_from_text"] is True and not conv.guards


def test_empty_message_gets_a_polite_reply_without_the_llm() -> None:
    conv, events = _conv([])
    asyncio.run(conv.user_turn(""))
    assert conv.messages[-1] == {"role": "assistant", "content": textcalls.EMPTY_REPLY}
    assert not conv.think_log and not conv.calls
