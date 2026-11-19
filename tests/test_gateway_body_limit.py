"""Issue #15: POST /mcp (and /v1/hint) refuse a body over max_body_bytes (default 1 MiB) with
413, from Content-Length before reading, or while streaming, never buffering it whole."""

from __future__ import annotations

import asyncio

import httpx

from gateway import main
from tests.test_gateway import call, make


async def send(gw, content, headers=None, path="/mcp"):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(gw.app(background=False)),
                                 base_url="http://gw") as c:
        return await c.post(path, content=content, headers={
            "content-type": "application/json", "Mcp-Session-Id": "s1", **(headers or {})})


def test_default_limit_is_1_mib():
    gw, _, _, _ = make()
    assert gw.cfg.max_body_bytes == 1024 * 1024


def test_oversized_content_length_is_413_before_reading():
    async def go():
        gw, _, _, seen = make(ready={"shell": 1}, max_body_bytes=1000)
        read = []

        async def body():
            read.append(1)
            yield b"x" * 10

        r = await send(gw, body(), headers={"content-length": "5000000"})
        assert r.status_code == 413 and read == [] and seen == []
    asyncio.run(go())


def test_streamed_body_over_the_limit_is_413():
    async def go():
        gw, _, _, seen = make(ready={"shell": 1}, max_body_bytes=1000)

        async def body():                      # chunked: no Content-Length
            for _ in range(100):
                yield b" " * 100

        r = await send(gw, body())
        assert r.status_code == 413 and seen == []
    asyncio.run(go())


def test_body_under_the_limit_still_works():
    import json
    async def go():
        gw, _, _, seen = make(ready={"shell": 1}, max_body_bytes=4096)
        r = await send(gw, json.dumps(call("shell")).encode())
        assert r.status_code == 200 and len(seen) == 1
    asyncio.run(go())


def test_hint_is_bounded_too():
    async def go():
        gw, _, _, _ = make(turn_hint=True, max_body_bytes=100)
        r = await send(gw, b'{"turn": 1' + b" " * 500 + b"}", path="/v1/hint")
        assert r.status_code == 413
    asyncio.run(go())


def test_env_sets_the_limit(monkeypatch):
    monkeypatch.setenv("SKYPIEA_MAX_BODY_BYTES", "2048")
    assert main.gateway_config().max_body_bytes == 2048


def test_chart_renders_an_integer_limit():
    import shutil
    import subprocess
    from pathlib import Path

    import pytest
    if shutil.which("helm") is None:
        pytest.skip("needs helm")
    chart = Path(__file__).resolve().parents[1] / "charts" / "skypiea"
    out = subprocess.run(["helm", "template", "x", str(chart)], check=True,
                         capture_output=True, text=True).stdout
    assert '{name: SKYPIEA_MAX_BODY_BYTES, value: "1048576"}' in out or \
        'value: "1048576"' in out.split("SKYPIEA_MAX_BODY_BYTES", 1)[1][:60]
