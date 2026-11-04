"""concurrent_check: a failed call is recorded (never dropped); metric sums parse."""

import asyncio

import httpx

from gateway import concurrent_check as cc


def test_failed_calls_are_recorded_and_metrics_sum():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "DELETE":
            return httpx.Response(200)
        name = req.headers["Mcp-Name"]
        if name == "shell":
            return httpx.Response(200, json={"error": {"code": -32000, "message": "boom"}})
        return httpx.Response(200, json={"result": {}}, headers={"X-Skypiea-Cold": "1"})

    calls = [{"tool_class": t, "t_ms": i * 10.0, "service_ms": 1.0}
             for i, t in enumerate(["user_lookup", "shell"])]
    rows: list = []

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            await cc.session(c, "http://gw", calls, 0.0, rows)
    asyncio.run(go())
    assert [r["ok"] for r in rows] == [True, False]
    assert rows[0]["cold"] and "boom" in rows[1]["err"]
    text = ('skypiea_scaleups_queued_total{tool="a"} 2.0\n'
            'skypiea_scaleups_queued_total{tool="b"} 1.0\n')
    assert cc.metric(text, "skypiea_scaleups_queued_total") == 3.0
