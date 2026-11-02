"""Start two real tool servers locally and call each with the official MCP client."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

pytest.importorskip("mcp", reason="pip install -r requirements-infra.txt")
pytest.importorskip("pandas")

ROOT = Path(__file__).resolve().parent.parent
TOOLS = {  # one light and one medium tool
    "user_lookup": {"user_id": "nope", "service_ms": 30},
    "order_lookup": {"order_id": "#W100007", "service_ms": 30},
}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def servers():
    procs = {}
    for tool in TOOLS:
        port = _free_port()
        p = subprocess.Popen([sys.executable, "-m", f"tools.{tool}.server"], cwd=ROOT,
                             env={**os.environ, "PORT": str(port)},
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        procs[tool] = (port, p)
    try:
        for tool, (port, p) in procs.items():
            deadline = time.monotonic() + 30
            while True:
                if p.poll() is not None:
                    pytest.fail(f"{tool} exited: {p.stdout.read().decode()[-2000:]}")
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1):
                        break
                except OSError:
                    if time.monotonic() > deadline:
                        pytest.fail(f"{tool} not healthy after 30 s")
                    time.sleep(0.05)
        yield {t: port for t, (port, _) in procs.items()}
    finally:
        for _, p in procs.values():
            p.terminate()
            p.wait(timeout=10)


async def _call(port: int, tool: str, args: dict):
    from mcp import Client

    async with Client(f"http://127.0.0.1:{port}/mcp", mode="2026-07-28") as c:
        listed = await c.list_tools()
        t0 = time.perf_counter()
        res = await c.call_tool(tool, args)
        return [t.name for t in listed.tools], res, time.perf_counter() - t0


@pytest.mark.parametrize("tool", list(TOOLS))
def test_real_mcp_call(servers, tool):
    names, res, elapsed = asyncio.run(_call(servers[tool], tool, TOOLS[tool]))
    assert names == [tool]
    assert not res.is_error, res.content
    data = json.loads(res.content[0].text)
    assert data  # deterministic fake data
    assert elapsed >= 0.03  # service_ms of real work was spent
    again = asyncio.run(_call(servers[tool], tool, TOOLS[tool]))[1]
    assert json.loads(again.content[0].text) == data  # deterministic


def test_healthz_and_metrics(servers):
    port = servers["user_lookup"]
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz") as r:
        body = json.loads(r.read())
    assert body["status"] == "ok" and body["startup_s"] > 0
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics") as r:
        text = r.read().decode()
    assert "skypiea_tool_startup_seconds" in text
