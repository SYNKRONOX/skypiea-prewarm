"""Issue #4: when the controller reports that it cannot scale a tool up (e.g. RBAC/IAM loss),
a cold call fails fast with a clear error instead of blocking for cold_timeout_s (120 s)."""

from __future__ import annotations

import asyncio
import time

from controller.core import PoolConfig
from controller.service import ControllerService, FakeScaler
from gateway.controller_client import RecordingController
from tests.test_gateway import call, make, post


class Failing(RecordingController):
    async def scale_failing(self, server: str) -> bool:
        return True


def test_cold_call_fails_fast_when_scaling_is_failing():
    async def go():
        gw, _, _, _ = make(cold_timeout_s=10, scale_check_s=0.05)
        gw.controller = Failing()
        t = time.monotonic()
        r = await post(gw, call("shell"))
        assert time.monotonic() - t < 2
        err = r.json()["error"]
        assert err["code"] == -32004 and "cannot scale" in err["message"]
        assert gw.inflight["shell"] == 0
    asyncio.run(go())


def test_controller_without_the_probe_still_waits_normally():
    async def go():
        gw, _, _, _ = make(cold_timeout_s=0.3, scale_check_s=0.05)
        r = await post(gw, call("shell"))
        assert r.json()["error"]["code"] == -32001
    asyncio.run(go())


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_controller_state_reports_scale_failures_and_clears_on_success():
    async def go():
        sc = FakeScaler(fail_times=2)
        svc = ControllerService(sc, PoolConfig(), clock=Clock(), admin_token="t")
        svc.warm("shell", 1, 30, "demand", 0)
        await svc.tick()
        assert svc.state()["shell"]["scale_failing"] is False   # one failure: transient
        await svc.tick()
        assert svc.state()["shell"]["scale_failing"] is True
        await svc.tick()                                   # the retry succeeds
        assert svc.state()["shell"]["scale_failing"] is False
    asyncio.run(go())
