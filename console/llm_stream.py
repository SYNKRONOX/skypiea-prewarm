"""Streaming OpenAI-compatible chat client for the console (text deltas reach the browser as
they arrive). Same configuration as agent/llm.py: AGENT_BASE_URL, AGENT_MODEL, optional
AGENT_API_KEY (sent as a Bearer token; never shown, logged or stored). Temperature 0 and a
fixed seed; qwen3 models with thinking disabled.

``think_ms`` is the time from sending the request to the complete reply: the gap the gateway
sees before the reply's first tool call (calls are sent once the reply is complete).
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from agent.llm import LLMError, ToolCall, _parse_args


@dataclass
class Reply:
    content: str
    tool_calls: list[ToolCall]
    think_ms: float
    first_token_ms: float | None
    message: dict[str, Any] = field(default_factory=dict)
    from_text: bool = False   # the tool call was written as JSON text, not sent as a tool call


def tool_call_from_text(content: str, tool_names: set[str]) -> tuple[ToolCall, str] | None:
    """llama3.1 via Ollama sometimes WRITES a tool call as JSON in its text instead of sending
    it as a tool call, e.g. '... {"name": "reservation_lookup", "parameters": {...}}'. If the
    text holds exactly such an object naming a known tool, return it (and the text before it).
    The console marks every recovered call as "from text" on screen and in the CSV."""
    dec = json.JSONDecoder()
    i = content.find("{")
    while i != -1:
        try:
            obj, end = dec.raw_decode(content, i)
        except ValueError:
            i = content.find("{", i + 1)
            continue
        if isinstance(obj, dict) and obj.get("name") in tool_names:
            args = obj.get("parameters", obj.get("arguments", {}))
            if isinstance(args, dict):
                return (ToolCall("text_0", obj["name"], args, json.dumps(args)),
                        content[:i].strip())
        i = content.find("{", end)
    return None


SEED = 7      # fixed seed for every request (Ollama supports it): repeatable tool turns


class StreamingLLM:
    def __init__(self, base_url: str, model: str, api_key: str | None = None,
                 timeout_s: float = 300.0, http: httpx.AsyncClient | None = None):
        if not base_url or not model:
            raise LLMError("AGENT_BASE_URL and AGENT_MODEL must be set")
        self.base_url, self.model, self._key = base_url.rstrip("/"), model, api_key or None
        self.http = http or httpx.AsyncClient(timeout=timeout_s)

    @classmethod
    def from_env(cls) -> StreamingLLM:
        return cls(os.environ.get("AGENT_BASE_URL", ""), os.environ.get("AGENT_MODEL", ""),
                   os.environ.get("AGENT_API_KEY") or None)

    def __repr__(self) -> str:
        key = "set" if self._key else "unset"
        return f"StreamingLLM({self.base_url!r}, {self.model!r}, key={key})"

    async def reply(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
                    on_text: Callable[[str], Awaitable[None]] | None = None) -> Reply:
        r = await self._reply(messages, tools, on_text)
        if not r.tool_calls and r.content:
            names = {t["function"]["name"] for t in tools}
            got = tool_call_from_text(r.content, names)
            if got:
                tc, before = got
                r.tool_calls, r.content, r.from_text = [tc], before, True
                r.message = {"role": "assistant", "content": before, "tool_calls": [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.name, "arguments": tc.raw_arguments}}]}
        return r

    async def _reply(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
                     on_text: Callable[[str], Awaitable[None]] | None = None) -> Reply:
        headers = {"content-type": "application/json"}
        if self._key:
            headers["authorization"] = f"Bearer {self._key}"
        body = {"model": self.model, "messages": messages, "tools": tools, "temperature": 0,
                "seed": SEED, "stream": True}
        if not tools:
            body.pop("tools")
        if self.model.startswith("qwen3"):
            # thinking DISABLED (W1 night): Ollama's OpenAI-compatible endpoint maps
            # reasoning_effort "none" to think=false; "think" is also sent for older builds
            body.update(think=False, reasoning_effort="none")
        t0 = time.monotonic()
        first = None
        text: list[str] = []
        calls: dict[int, dict[str, Any]] = {}
        try:
            async with self.http.stream("POST", f"{self.base_url}/chat/completions", json=body,
                                        headers=headers) as r:
                if r.status_code != 200:
                    raise LLMError(f"LLM HTTP {r.status_code}: {(await r.aread())[:200]!r}")
                async for ln in r.aiter_lines():
                    if not ln.startswith("data: ") or ln.strip() == "data: [DONE]":
                        continue
                    try:
                        d = json.loads(ln[6:])
                        delta = d["choices"][0].get("delta") or {}
                    except (ValueError, KeyError, IndexError, TypeError):
                        continue
                    if first is None:
                        first = (time.monotonic() - t0) * 1000
                    if delta.get("content"):
                        text.append(delta["content"])
                        if on_text:
                            await on_text(delta["content"])
                    for tc in delta.get("tool_calls") or []:
                        c = calls.setdefault(int(tc.get("index", len(calls))),
                                             {"id": "", "name": "", "args": ""})
                        c["id"] = tc.get("id") or c["id"]
                        fn = tc.get("function") or {}
                        c["name"] += fn.get("name") or ""
                        a = fn.get("arguments")
                        c["args"] += a if isinstance(a, str) else (json.dumps(a) if a else "")
        except httpx.HTTPError as e:
            raise LLMError(f"LLM request failed: {type(e).__name__}") from None
        think_ms = (time.monotonic() - t0) * 1000
        tcs = []
        for i in sorted(calls):
            args, raw = _parse_args(calls[i]["args"])
            tcs.append(ToolCall(calls[i]["id"] or f"call_{i}", calls[i]["name"], args, raw))
        content = "".join(text)
        msg: dict[str, Any] = {"role": "assistant", "content": content}
        if tcs:
            msg["tool_calls"] = [{"id": c.id, "type": "function",
                                  "function": {"name": c.name, "arguments": c.raw_arguments}}
                                 for c in tcs]
        return Reply(content, tcs, think_ms, first, msg)


class ScriptedStreamingLLM:
    """Offline stand-in (tests, `make live` smoke): one tool call per reply from a fixed list,
    then a text answer. ``think_ms`` is reported, never slept: this is for tests only, and the
    console labels it 'scripted'."""

    def __init__(self, turns: list[list[tuple[str, dict[str, Any]]]], model: str = "scripted",
                 answers: list[str] | None = None):
        self.turns, self.model, self._t, self._i = turns, model, 0, 0
        self.answers = answers or []          # final text per turn (default "done")

    async def reply(self, messages, tools, on_text=None) -> Reply:
        calls = self.turns[self._t] if self._t < len(self.turns) else []
        if self._i < len(calls):
            name, args = calls[self._i]
            self._i += 1
            tc = ToolCall(f"c{self._t}_{self._i}", name, dict(args), json.dumps(args))
            fn = {"name": name, "arguments": tc.raw_arguments}
            msg = {"role": "assistant", "content": "",
                   "tool_calls": [{"id": tc.id, "type": "function", "function": fn}]}
            return Reply("", [tc], 0.0, None, msg)
        text = self.answers[self._t] if self._t < len(self.answers) else "done"
        self._t, self._i = self._t + 1, 0
        if on_text:
            await on_text(text)
        return Reply(text, [], 0.0, None, {"role": "assistant", "content": text})
