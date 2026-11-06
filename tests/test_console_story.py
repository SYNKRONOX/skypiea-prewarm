"""Story mode (W1 #16): prepared lines go to the live model through the autopilot; Act 3
kills the predictor after its first turn and restarts it; the codefix demo has one act."""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("starlette")

from agent.tasks.stories import STORIES  # noqa: E402


def _console(monkeypatch, demo="skyair"):
    from console import app as appmod
    from console.app import Console
    from console.llm_stream import ScriptedStreamingLLM
    from tests.test_console_idle import IdleStack

    c = Console(ScriptedStreamingLLM([]), base=IdleStack("base"), pred=IdleStack("pred"),
                start_poller=False)
    c.settings.update(demo=demo, autopilot_cpm=0)
    actions = []

    async def kubectl(*a, **k):
        actions.append(a)
        return 0, "ok"
    monkeypatch.setattr(appmod, "kubectl", kubectl)

    async def health():
        return {"status": "unreachable"}
    c.model_health = health
    return c, actions


def test_act3_kills_after_turn_one_and_restarts(monkeypatch) -> None:
    c, actions = _console(monkeypatch)
    seen = []

    async def go():
        async def pub(ev):
            if ev["type"] == "turn_start":
                seen.append((ev["turn"], bool(actions)))
        c.hub.publish = pub
        c.poller.all_zero = lambda: True
        c.new_conversation()
        await c.story(3)
    asyncio.run(go())
    act = STORIES["skyair"][2]
    assert [t for t, _ in seen] == list(range(1, len(act["script"]) + 1))
    assert seen[0] == (1, False) and all(k for _, k in seen[1:])      # killed after turn 1
    scale = [a for a in actions if "scale" in a]
    assert scale[0][-1] == "--replicas=0" and scale[-1][-1] == "--replicas=1"
    assert c.conv.turn == len(act["script"]) and c.settings["story"] is None


def test_story_route_and_codefix_act(monkeypatch) -> None:
    from starlette.testclient import TestClient

    from console.app import make_app
    c, _ = _console(monkeypatch, "codefix")
    with TestClient(make_app(c)) as cl:
        s = cl.get("/api/state").json()
        assert s["demo"]["agent"] == "Dev assistant" and len(s["demo"]["stories"]) == 1
        assert cl.post("/api/story", json={"act": 3}).status_code == 400
        p = cl.get("/api/personas").json()
        assert p["agent"] == "Dev assistant"
        assert "Fix the failing merge_sort test" in p["personas"][0]["requests"]
