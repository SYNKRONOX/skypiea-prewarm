"""Offline, deterministic tests for the agent (agent/): no cluster, no LLM, no network.

The gateway and the LLM endpoint are httpx.MockTransport fakes."""

from __future__ import annotations

import json

import httpx
import pytest

from agent import tasks
from agent.llm import (
    LLMError,
    NoToolCallError,
    OpenAICompatibleLLM,
    ScriptedLLM,
)
from agent.loop import STEP_FIELDS, run_session
from agent.mcp_client import SESSION_HEADER, McpClient

CTX = {"run_id": "t", "policy": "scale_to_zero", "llm": "scripted", "model": "m",
       "profile": "prd"}


class FakeGateway:
    """POST /mcp like the Skypiea gateway: session header, tools/list, tools/call with the
    X-Skypiea-* headers; the first call of each tool is cold."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.sessions_seen: list[str | None] = []
        self.deleted: list[str] = []
        self.warm: set[str] = set()

    def __call__(self, req: httpx.Request) -> httpx.Response:
        sid = req.headers.get(SESSION_HEADER)
        if req.method == "DELETE":
            self.deleted.append(sid or "")
            return httpx.Response(204)
        self.sessions_seen.append(sid)
        sid = sid or "sess-1"
        body = json.loads(req.content)
        if body["method"] == "tools/list":
            return httpx.Response(200, headers={SESSION_HEADER: sid}, json={
                "jsonrpc": "2.0", "id": body["id"],
                "result": {"tools": [{"name": n} for n in ("order_lookup", "policy_check")]}})
        name = body["params"]["name"]
        self.calls.append((name, body["params"]["arguments"]))
        cold = name not in self.warm
        self.warm.add(name)
        return httpx.Response(200, headers={
            SESSION_HEADER: sid, "X-Skypiea-Cold": "1" if cold else "0",
            "X-Skypiea-Added-Ms": "2500.0" if cold else "0.0", "X-Skypiea-Held-Ms": "0.0",
            "X-Skypiea-Predicted": "0"}, json={
            "jsonrpc": "2.0", "id": body["id"],
            "result": {"content": [{"type": "text", "text": "{}"}],
                       "structuredContent": {"tool": name, "ok": True}}})


def client(gw: FakeGateway) -> McpClient:
    return McpClient("http://gw/mcp", client=httpx.Client(transport=httpx.MockTransport(gw)))


def test_mcp_client_keeps_session_and_reads_headers() -> None:
    gw = FakeGateway()
    c = client(gw)
    assert c.list_tools() == ["order_lookup", "policy_check"]
    res, info = c.call_tool("order_lookup", {"order_id": "#W1"})
    assert info.ok and info.cold is True and info.added_ms == 2500.0
    assert res["structuredContent"]["tool"] == "order_lookup"
    _, info2 = c.call_tool("order_lookup", {})
    assert info2.cold is False
    assert gw.sessions_seen == [None, "sess-1", "sess-1"]
    c.close()
    assert gw.deleted == ["sess-1"]


@pytest.mark.parametrize("scenario", ["A", "B"])
def test_scripted_session_follows_graph(scenario: str) -> None:
    task = tasks.get(scenario)
    gw = FakeGateway()
    llm = ScriptedLLM(task.scripted(0), think_ms=1234, sleep=False)
    res = run_session(llm, client(gw), task, 0, CTX)
    assert [n for n, _ in gw.calls] == task.canonical
    s = res.summary
    assert s["end_reason"] == "finish" and s["followed_graph"] and s["canonical_order"]
    assert [r["tool"] for r in res.steps] == task.canonical
    assert all(r["think_ms"] == 1234 for r in res.steps)
    assert all(r["cold"] is True for r in res.steps)        # each tool once -> cold
    assert set(res.steps[0]) == set(STEP_FIELDS)


def test_rows_hold_no_prompts_or_payloads() -> None:
    task = tasks.get("A")
    res = run_session(ScriptedLLM(task.scripted(3), think_ms=0, sleep=False),
                      client(FakeGateway()), task, 3, CTX)
    blob = json.dumps([res.steps, res.summary])
    assert "#W" not in blob and task.user_prompt(3) not in blob


def test_graph_check() -> None:
    a = tasks.get("A")
    assert a.follows_graph(["order_lookup"])
    assert a.follows_graph(["order_lookup", "refund_issue"])
    assert not a.follows_graph(["policy_check", "order_lookup", "refund_issue"])
    assert not a.follows_graph([])
    b = tasks.get("B")
    assert b.follows_graph(b.canonical)
    assert not b.follows_graph(["read_file", "format_diff", "run_tests", "post_comment"])
    assert not b.follows_graph(["read_file", "run_tests"])        # B must reach post_comment


def test_unknown_tool_not_sent_and_args_filtered() -> None:
    task = tasks.get("A")
    gw = FakeGateway()
    seq = [("lookup the order", {}), ("order_lookup", {"order_id": "#W1", "bogus": 1})]
    res = run_session(ScriptedLLM(seq, think_ms=0, sleep=False), client(gw), task, 0, CTX)
    assert gw.calls == [("order_lookup", {"order_id": "#W1"})]
    assert res.steps[0]["error"] == "unknown tool (not sent)"
    assert res.steps[0]["tool"] == "lookup?the?order"
    assert res.steps[1]["args_dropped"] == 1


def test_step_limit() -> None:
    task = tasks.get("B")
    seq = [("read_file", {"path": "a"})] * 20
    res = run_session(ScriptedLLM(seq, think_ms=0, sleep=False), client(FakeGateway()), task, 0,
                      CTX, max_steps=3)
    assert res.summary["end_reason"] == "step_limit" and res.summary["tool_calls"] == 3


def _llm(handler) -> OpenAICompatibleLLM:
    return OpenAICompatibleLLM("http://llm/v1", "m", api_key="sk-secret-123",
                               client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_openai_client_parses_tool_calls_and_sends_temperature_0() -> None:
    seen = {}

    def h(req: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(req.content)
        seen["auth"] = req.headers.get("authorization")
        return httpx.Response(200, json={"choices": [{"message": {
            "role": "assistant", "content": "",
            "tool_calls": [{"id": "c1", "type": "function", "function": {
                "name": "order_lookup", "arguments": "{\"order_id\": \"#W9\"}"}}]}}]})

    r = _llm(h).step([{"role": "user", "content": "x"}], tasks.get("A").tools)
    assert seen["body"]["temperature"] == 0 and seen["body"]["model"] == "m"
    assert seen["auth"] == "Bearer sk-secret-123"
    assert r.tool_calls[0].name == "order_lookup" and r.tool_calls[0].arguments == {
        "order_id": "#W9"}
    assert r.message["tool_calls"][0]["function"]["arguments"] == "{\"order_id\": \"#W9\"}"


def test_openai_client_no_tool_calls_is_a_clear_error() -> None:
    llm = _llm(lambda req: httpx.Response(200, json={"choices": [{"message": {
        "role": "assistant", "content": "I will look up your order."}}]}))
    with pytest.raises(NoToolCallError, match="no tool calls"):
        llm.step([], [])


def test_openai_client_never_shows_the_key() -> None:
    llm = _llm(lambda req: httpx.Response(500, text="boom"))
    assert "sk-secret" not in repr(llm)
    with pytest.raises(LLMError) as e:
        llm.step([], [])
    assert "sk-secret" not in str(e.value)


def test_openai_client_needs_url_and_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_BASE_URL", raising=False)
    monkeypatch.delenv("AGENT_MODEL", raising=False)
    with pytest.raises(LLMError, match="AGENT_BASE_URL"):
        OpenAICompatibleLLM.from_env()


def test_no_tool_call_ends_session_with_reason() -> None:
    task = tasks.get("A")
    llm = _llm(lambda req: httpx.Response(200, json={"choices": [{"message": {
        "role": "assistant", "content": "hello"}}]}))
    res = run_session(llm, client(FakeGateway()), task, 0, CTX)
    assert res.summary["end_reason"] == "no_tool_call" and res.steps == []


def test_agent_imports_no_platform_internals() -> None:
    import pathlib
    import re
    for p in pathlib.Path("agent").rglob("*.py"):
        src = p.read_text()
        assert not re.search(r"^\s*(from|import)\s+(gateway|controller)\b", src, re.M), p
