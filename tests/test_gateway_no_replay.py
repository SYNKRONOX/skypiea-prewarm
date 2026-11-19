"""Issue #16: a tools/call whose connection dropped AFTER the request was sent may have run on
the tool: the gateway must not send it again. It answers a clear JSON-RPC error instead.
Connection failures before anything was sent (refused, connect timeout) are still retried."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from tests.test_gateway import call, counter, make, post


@pytest.mark.parametrize("exc", [httpx.RemoteProtocolError("Server disconnected"),
                                 httpx.ReadError("reset"), httpx.ReadTimeout("slow")])
def test_dropped_after_send_is_not_retried(exc):
    async def go():
        attempts = []

        def dropped(req: httpx.Request) -> httpx.Response:
            attempts.append(1)
            raise exc

        gw, _, _, _ = make(ready={"shell": 1}, connect_retry_s=2)
        gw.http = httpx.AsyncClient(transport=httpx.MockTransport(dropped))
        r = await post(gw, call("shell"))
        assert len(attempts) == 1                               # sent exactly once
        err = r.json()["error"]
        assert err["code"] == -32003
        assert "may have run" in err["message"] and "not retried" in err["message"]
        assert counter(gw.metrics, "skypiea_gateway_connect_retries_total", tool="shell") == 0
        assert gw.inflight["shell"] == 0
    asyncio.run(go())


def test_connect_errors_are_still_retried():
    async def go():
        attempts = []

        def flaky(req: httpx.Request) -> httpx.Response:
            attempts.append(1)
            if len(attempts) < 2:
                raise httpx.ConnectTimeout("connect timeout")
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {
                "content": [], "isError": False}})

        gw, _, _, _ = make(ready={"shell": 1})
        gw.http = httpx.AsyncClient(transport=httpx.MockTransport(flaky))
        r = await post(gw, call("shell"))
        assert "result" in r.json() and len(attempts) == 2
    asyncio.run(go())
