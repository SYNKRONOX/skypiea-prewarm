"""LLM clients for the Skypiea agent: an OpenAI-compatible tool-calling client and a scripted
stand-in for CI and offline demos.

    AGENT_BASE_URL   e.g. http://localhost:11434/v1 (Ollama) - required for the live client
    AGENT_MODEL      e.g. llama3.1:8b                         - required for the live client
    AGENT_API_KEY    optional; sent as a Bearer token, never printed, logged or stored

Both clients return an ``LLMReply`` (tool calls + how long the model "thought"). A reply with
no tool calls is an error (``NoToolCallError``): the agent finishes by calling the ``finish``
tool, so a free-text answer means the model did not follow the tool protocol.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

FINISH = "finish"


class LLMError(RuntimeError):
    """The model endpoint failed or answered something unusable."""


class NoToolCallError(LLMError):
    """The model answered without any tool call."""


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    raw_arguments: str = ""


@dataclass(frozen=True)
class LLMReply:
    tool_calls: list[ToolCall]
    think_ms: float
    content: str | None = None
    # the assistant message to append to the conversation (OpenAI chat format)
    message: dict[str, Any] = field(default_factory=dict)


def _parse_args(raw: Any) -> tuple[dict[str, Any], str]:
    if isinstance(raw, dict):
        return raw, json.dumps(raw)
    if raw in (None, ""):
        return {}, "{}"
    try:
        val = json.loads(raw)
    except (TypeError, ValueError):
        return {}, str(raw)
    return (val if isinstance(val, dict) else {}), str(raw)


class OpenAICompatibleLLM:
    """POST {base_url}/chat/completions with tools; temperature 0 (deterministic as the server
    allows). Works with Ollama's /v1 endpoint and other OpenAI-compatible servers."""

    def __init__(self, base_url: str, model: str, api_key: str | None = None,
                 timeout_s: float = 300.0, temperature: float = 0.0,
                 client: httpx.Client | None = None):
        if not base_url or not model:
            raise LLMError("AGENT_BASE_URL and AGENT_MODEL must be set for the live LLM")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self._key = api_key or None
        self._http = client or httpx.Client(timeout=timeout_s)

    @classmethod
    def from_env(cls, **kw: Any) -> OpenAICompatibleLLM:
        return cls(os.environ.get("AGENT_BASE_URL", ""), os.environ.get("AGENT_MODEL", ""),
                   os.environ.get("AGENT_API_KEY") or None, **kw)

    def __repr__(self) -> str:   # never show the key
        return (f"OpenAICompatibleLLM(base_url={self.base_url!r}, model={self.model!r}, "
                f"api_key={'set' if self._key else 'unset'})")

    @property
    def name(self) -> str:
        return self.model

    def step(self, messages: Sequence[dict[str, Any]], tools: Sequence[dict[str, Any]]
             ) -> LLMReply:
        headers = {"content-type": "application/json"}
        if self._key:
            headers["authorization"] = f"Bearer {self._key}"
        body = {"model": self.model, "messages": list(messages), "tools": list(tools),
                "tool_choice": "auto", "temperature": self.temperature, "stream": False}
        t = time.monotonic()
        try:
            r = self._http.post(f"{self.base_url}/chat/completions", json=body, headers=headers)
        except httpx.HTTPError as e:
            raise LLMError(f"LLM request failed: {type(e).__name__}: {e}") from None
        think_ms = (time.monotonic() - t) * 1000
        if r.status_code != 200:
            # the body may echo the request; keep only the status and a short, key-free excerpt
            raise LLMError(f"LLM HTTP {r.status_code}: {r.text[:200]}")
        try:
            msg = r.json()["choices"][0]["message"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise LLMError(f"LLM answer has no choices[0].message: {r.text[:200]}") from None
        calls = []
        for i, tc in enumerate(msg.get("tool_calls") or []):
            fn = tc.get("function") or {}
            args, raw = _parse_args(fn.get("arguments"))
            calls.append(ToolCall(tc.get("id") or f"call_{i}", str(fn.get("name", "")), args, raw))
        content = msg.get("content")
        if not calls:
            raise NoToolCallError(
                "the model returned no tool calls (it must call a tool or `finish`); "
                f"it answered with text of {len(content or '')} chars")
        out = {"role": "assistant", "content": content or "",
               "tool_calls": [{"id": c.id, "type": "function",
                               "function": {"name": c.name, "arguments": c.raw_arguments}}
                              for c in calls]}
        return LLMReply(calls, think_ms, content, out)


class ScriptedLLM:
    """Returns a fixed tool sequence (then ``finish``), one tool call per step, after a fake
    think time. For CI and offline demos: no network, deterministic.

    ``think_ms``: a number, or a callable(step_index) -> ms. ``sleep=False`` reports the think
    time without waiting (unit tests)."""

    def __init__(self, sequence: Sequence[tuple[str, dict[str, Any]]],
                 think_ms: float | Callable[[int], float] = 1500.0, sleep: bool = True,
                 name: str = "scripted"):
        self.sequence = list(sequence)
        self.think = think_ms if callable(think_ms) else (lambda _i, v=float(think_ms): v)
        self.sleep = sleep
        self._name = name
        self._i = 0

    @property
    def name(self) -> str:
        return self._name

    def reset(self) -> None:
        self._i = 0

    def step(self, messages: Sequence[dict[str, Any]], tools: Sequence[dict[str, Any]]
             ) -> LLMReply:
        ms = float(self.think(self._i))
        if self.sleep and ms > 0:
            time.sleep(ms / 1000)
        if self._i < len(self.sequence):
            name, args = self.sequence[self._i]
        else:
            name, args = FINISH, {"summary": "done"}
        call = ToolCall(f"call_{self._i}", name, dict(args), json.dumps(args))
        self._i += 1
        msg = {"role": "assistant", "content": "",
               "tool_calls": [{"id": call.id, "type": "function",
                               "function": {"name": name, "arguments": call.raw_arguments}}]}
        return LLMReply([call], ms, None, msg)
