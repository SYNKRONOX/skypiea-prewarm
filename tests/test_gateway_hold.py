"""FR-9 hold metric (W1 P0 #2). `held_ms` is the hold itself and never exceeds the cap;
`hold_late_ms` is how late a busy event loop resumed the call after the hold ended; and
`hold_wait_ms` (arrival -> resumed) is what the AWS runs recorded as held_ms (max 397.2 ms).
A blocked event loop reproduces that overrun offline."""

from __future__ import annotations

import asyncio
import time

import httpx

from tests.test_gateway import call, counter, make


def _hold_sample(gw, cap: float = 150) -> tuple[float, float]:
    le = gw.metrics.registry.get_sample_value(
        "skypiea_gateway_hold_ms_bucket", {"tool": "run_tests", "le": str(float(cap))}) or 0.0
    inf = gw.metrics.registry.get_sample_value(
        "skypiea_gateway_hold_ms_bucket", {"tool": "run_tests", "le": "+Inf"}) or 0.0
    return le, inf


def test_hold_never_exceeds_the_cap_even_when_the_event_loop_is_blocked() -> None:
    async def go():
        gw, _, _, _ = make(ready_after=0.6)
        loop = asyncio.get_running_loop()
        # a CPU-bound stall of the event loop while the call is held (stands in for many
        # calls arriving at once or a paused node): the hold's timer fires late
        loop.call_later(0.05, time.sleep, 0.35)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(gw.app(background=False)),
                                     base_url="http://gw") as c:
            r = await c.post("/mcp", json=call("run_tests"),
                             headers={"Mcp-Session-Id": "s1"})
        await gw.drain()
        rec = gw.sessions.get("s1").calls[0]
        return r, rec, gw

    r, rec, gw = asyncio.run(go())
    assert r.headers["X-Skypiea-Cold"] == "1"
    held, late = float(r.headers["X-Skypiea-Held-Ms"]), float(r.headers["X-Skypiea-Hold-Late-Ms"])
    assert held <= 150.0 and rec["held_ms"] <= 150.0
    assert late >= 200.0                                  # the stall shows up here, measured
    assert rec["hold_wait_ms"] >= rec["held_ms"] + rec["hold_late_ms"] - 0.2
    assert rec["hold_wait_ms"] > 150.0                    # the pre-W1 definition overruns
    le, inf = _hold_sample(gw)
    assert inf == 1 and le == inf                         # the histogram never above the cap


def test_concurrent_holds_all_within_the_cap() -> None:
    async def go():
        gw, _, _, _ = make(ready_after=0.5)
        app = gw.app(background=False)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),
                                     base_url="http://gw") as c:
            rs = await asyncio.gather(*[c.post("/mcp", json=call("run_tests"),
                                               headers={"Mcp-Session-Id": f"s{i}"})
                                        for i in range(8)])
        await gw.drain()
        return rs, gw

    rs, gw = asyncio.run(go())
    assert all(float(r.headers["X-Skypiea-Held-Ms"]) <= 150.0 for r in rs)
    le, inf = _hold_sample(gw)
    assert inf == 8 and le == 8
    assert counter(gw.metrics, "skypiea_gateway_hold_ms_count", tool="run_tests") == 8


def test_a_pod_ready_during_the_hold_ends_it_early() -> None:
    async def go():
        gw, _, _, _ = make(ready_after=0.04)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(gw.app(background=False)),
                                     base_url="http://gw") as c:
            r = await c.post("/mcp", json=call("run_tests"), headers={"Mcp-Session-Id": "s1"})
        await gw.drain()
        return r

    r = asyncio.run(go())
    assert r.headers["X-Skypiea-Cold"] == "0"
    assert 20.0 <= float(r.headers["X-Skypiea-Held-Ms"]) < 150.0
    assert float(r.headers["X-Skypiea-Hold-Late-Ms"]) == 0.0


def test_hold_report_explains_the_recorded_aws_values() -> None:
    from pathlib import Path

    from gateway.hold_report import report
    p = Path(__file__).resolve().parent.parent / "results" / "scenario_aws_calls.csv"
    r = report(p)
    assert r["max_held"]["held_ms"] == 397.2           # the recorded value, not edited
    assert r["held_calls"] == 675 and r["above_150_5"] == 541
    assert r["overrun_ms"]["p50"] <= 2.0
