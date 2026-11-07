"""Model dropdown (W1 #12): tool-capable Ollama models, the optional hosted endpoint (key never
exposed), switching resets the conversation, the model is in every exported row."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

pytest.importorskip("starlette")

from console.models import list_models, make_llm  # noqa: E402


def ollama(req: httpx.Request) -> httpx.Response:
    if req.url.path == "/api/tags":
        return httpx.Response(200, json={"models": [
            {"name": "qwen2.5:7b", "details": {"parameter_size": "7.6B"}},
            {"name": "llama3.1:8b", "details": {"parameter_size": "8.0B"}},
            {"name": "gemma:2b", "details": {}}]})
    name = json.loads(req.content)["model"]
    caps = ["completion"] + ([] if name == "gemma:2b" else ["tools"])
    return httpx.Response(200, json={"capabilities": caps})


def test_lists_tool_capable_local_models_and_a_hosted_endpoint_without_the_key() -> None:
    http = httpx.AsyncClient(transport=httpx.MockTransport(ollama))
    env = {"AGENT_BASE_URL": "https://llm.example.invalid/v1", "AGENT_MODEL": "big-model",
           "AGENT_API_KEY": "sk-test-not-real"}
    ms = asyncio.run(list_models(http, "llama3.1:8b", "http://ollama.test", env))
    by = {m["id"]: m for m in ms}
    assert by["ollama:qwen2.5:7b"]["selectable"] and by["ollama:llama3.1:8b"]["current"]
    assert not by["ollama:gemma:2b"]["selectable"]
    assert by["hosted"]["model"] == "big-model" and by["hosted"]["key"] == "set"
    assert "sk-test-not-real" not in json.dumps(ms)
    llm = make_llm(by["hosted"], env)
    assert "sk-test" not in repr(llm) and llm.model == "big-model"
    assert make_llm(by["ollama:qwen2.5:7b"], env)._key is None     # key only to the host


def test_no_hosted_entry_for_local_ollama_or_without_a_key() -> None:
    http = httpx.AsyncClient(transport=httpx.MockTransport(ollama))
    for env in ({"AGENT_BASE_URL": "http://localhost:11434/v1", "AGENT_MODEL": "x",
                 "AGENT_API_KEY": "k"},
                {"AGENT_BASE_URL": "https://llm.example.invalid/v1", "AGENT_MODEL": "x"}):
        ms = asyncio.run(list_models(http, None, "http://ollama.test", env))
        assert all(m["id"] != "hosted" for m in ms)


def test_switching_model_resets_and_rows_carry_the_model() -> None:
    from console.app import Console
    from console.llm_stream import ScriptedStreamingLLM
    from tests.test_console_idle import IdleStack

    c = Console(ScriptedStreamingLLM([], model="model-a"), base=IdleStack("base"),
                pred=IdleStack("pred"), start_poller=False)

    async def go():
        async def pub(ev):
            pass
        c.hub.publish = pub
        c.poller.all_zero = lambda: True
        first = c.new_conversation()
        await c.set_model({"id": "x", "model": "model-b", "provider": "test"},
                          llm=ScriptedStreamingLLM([], model="model-b"))
        assert c.conv is not first and c.conv.llm.model == "model-b"
        assert c.settings["llm_model"] == "model-b"
        await c.chat("hello")
        return c.conv
    conv = asyncio.run(go())
    assert conv.turns[0]["llm_model"] == "model-b"
