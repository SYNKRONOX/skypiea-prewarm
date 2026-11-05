"""End to end on one machine: official MCP client -> gateway (HTTP) -> real tool server process,
with the predictor placeholder reached over HTTP. Functional only; no timing is asserted."""

from __future__ import annotations

import pytest  # noqa: E402

pytest.importorskip("httpx", reason="make setup")
pytest.importorskip("starlette", reason="make setup")
pytest.importorskip("uvicorn", reason="make setup")

import asyncio
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

pytest.importorskip("mcp")

import uvicorn  # noqa: E402

from gateway.app import Gateway, GatewayConfig  # noqa: E402
from gateway.controller_client import RecordingController  # noqa: E402
from gateway.predictor import HttpPredictorClient  # noqa: E402
from gateway.predictor_stub import StubPredictor, create_app  # noqa: E402
from gateway.readiness import ReadyTracker  # noqa: E402
from gateway.routing import Router  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def serve(app, port: int) -> uvicorn.Server:
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=srv.run, daemon=True).start()
    while not srv.started:
        time.sleep(0.01)
    return srv


@pytest.fixture(scope="module")
def tool_server():
    port = free_port()
    p = subprocess.Popen([sys.executable, "-m", "tools.user_lookup.server"], cwd=ROOT,
                         env={**os.environ, "PORT": str(port)}, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    for _ in range(300):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=0.5)
            break
        except OSError:
            time.sleep(0.05)
    yield port
    p.terminate()
    p.wait(timeout=10)


def test_official_client_through_gateway_with_http_predictor(tool_server):
    pred_port, gw_port = free_port(), free_port()
    stub = StubPredictor("keep_alive", {"ttl_s": 30})
    pred = serve(create_app(stub), pred_port)
    tracker = ReadyTracker()
    tracker.set_ready("user_lookup", 1)
    ctrl = RecordingController()
    router = Router("ns", url_template=f"http://127.0.0.1:{tool_server}/mcp")
    holder = {}

    async def build():  # the gateway's httpx client must live on the server's loop
        gw = Gateway(GatewayConfig(), router, HttpPredictorClient(
            f"http://127.0.0.1:{pred_port}", 1.0), ctrl, tracker)
        holder["gw"] = gw
        return gw.app(background=False)

    loop_app = {}

    class Lazy:
        async def __call__(self, scope, receive, send):
            if "app" not in loop_app:
                loop_app["app"] = await build()
            await loop_app["app"](scope, receive, send)

    gwsrv = serve(Lazy(), gw_port)
    try:
        from mcp import Client

        async def go():
            async with Client(f"http://127.0.0.1:{gw_port}/mcp", mode="2026-07-28") as c:
                r = await c.call_tool("user_lookup", {"user_id": "x", "service_ms": 5})
                assert not r.is_error, r.content
                assert json.loads(r.content[0].text)["found"] is True
        asyncio.run(go())
        for _ in range(100):           # predictor runs after the reply; wait for its effect
            if ctrl.warms:
                break
            time.sleep(0.02)
        assert ctrl.warms == [("user_lookup", 1, 30, "predict:stub-keep_alive")]
    finally:
        gwsrv.should_exit = pred.should_exit = True
