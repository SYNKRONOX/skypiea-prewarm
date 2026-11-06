"""Gateway early connect (off by default): a cold call uses a not-yet-Ready pod as soon as its
/healthz answers, instead of waiting for Ready."""

from __future__ import annotations

import pytest

pytest.importorskip("httpx")
pytest.importorskip("starlette")

import asyncio  # noqa: E402
import json  # noqa: E402

import httpx  # noqa: E402

from tests.test_gateway import call, counter, make, post  # noqa: E402


def _upstream(seen, healthy_after: dict):
    def h(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/healthz":
            ok = asyncio.get_event_loop().time() >= healthy_after.get(req.url.host, 1e18)
            return httpx.Response(200 if ok else 503)
        body = json.loads(req.content)
        seen.append(str(req.url))
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"],
                                         "result": {"content": [], "isError": False}})
    return h


def _gw(early: bool):
    gw, ctrl, tracker, _ = make(None, cold_timeout_s=3, early_connect=early, early_poll_s=0.02)
    seen: list = []
    healthy: dict = {}
    gw.http = httpx.AsyncClient(transport=httpx.MockTransport(_upstream(seen, healthy)))
    return gw, tracker, seen, healthy


def test_off_by_default_waits_for_ready() -> None:
    gw, tracker, seen, healthy = _gw(False)

    async def go():
        loop = asyncio.get_running_loop()
        tracker.set_pending("user_lookup", ["10.0.0.9:8080"])
        healthy["10.0.0.9"] = loop.time()                     # healthy at once
        loop.call_later(0.5, tracker.set_ready, "user_lookup", 1, ["10.0.0.9:8080"])
        t = loop.time()
        r = await post(gw, call("user_lookup"))
        return r, loop.time() - t
    r, dt = asyncio.run(go())
    assert dt >= 0.45 and counter(gw.metrics, "skypiea_gateway_early_connects_total",
                                  tool="user_lookup") == 0


def test_early_connect_uses_the_pod_before_ready() -> None:
    gw, tracker, seen, healthy = _gw(True)

    async def go():
        loop = asyncio.get_running_loop()
        tracker.set_pending("user_lookup", ["10.0.0.9:8080"])
        healthy["10.0.0.9"] = loop.time() + 0.1               # app answers after 0.1 s
        loop.call_later(1.0, tracker.set_ready, "user_lookup", 1, ["10.0.0.9:8080"])
        t = loop.time()
        r = await post(gw, call("user_lookup"))
        return r, loop.time() - t
    r, dt = asyncio.run(go())
    assert "result" in r.json() and r.headers["X-Skypiea-Cold"] == "1"
    assert dt < 0.6 and seen[-1] == "http://10.0.0.9:8080/mcp"
    assert counter(gw.metrics, "skypiea_gateway_early_connects_total", tool="user_lookup") == 1


def test_early_connect_falls_back_to_ready_when_no_pod_answers() -> None:
    gw, tracker, seen, healthy = _gw(True)

    async def go():
        loop = asyncio.get_running_loop()
        tracker.set_pending("user_lookup", ["10.0.0.9:8080"])   # never healthy
        loop.call_later(0.3, tracker.set_ready, "user_lookup", 1, ["10.0.0.7:8080"])
        return await post(gw, call("user_lookup"))
    r = asyncio.run(go())
    assert "result" in r.json() and seen[-1] == "http://10.0.0.7:8080/mcp"
