"""Issues #23 and #5: POST /v1/warm needs the admin token and validates/bounds every field
(out of range -> 400, never 500); the metric label is a fixed set and the refused buffer is
drained, so untrusted input cannot grow controller memory."""

from __future__ import annotations

import asyncio

import pytest

from controller.core import PoolConfig
from controller.service import ControllerService, FakeScaler

TOK = "a" * 48
H = {"X-Admin-Token": TOK}


class Clock:
    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t


def run(fn, token=TOK, cfg=None):
    from aiohttp.test_utils import TestClient, TestServer

    async def go():
        svc = ControllerService(FakeScaler(), cfg or PoolConfig(), clock=Clock(0),
                                admin_token=token)
        async with TestClient(TestServer(svc.web_app())) as c:
            await fn(svc, c)
    asyncio.run(go())


OK = {"tool_class": "shell", "copies": 1, "ttl_s": 30, "reason": "predict:v1"}


def test_warm_needs_the_admin_token():
    async def fn(svc, c):
        assert (await c.post("/v1/warm", json=OK)).status == 401
        assert (await c.post("/v1/warm", json=OK, headers={"X-Admin-Token": "x"})).status == 401
        assert (await c.post("/v1/warm", json=OK, headers=H)).status == 200
    run(fn)


def test_no_token_configured_disables_warm():
    async def fn(svc, c):
        assert (await c.post("/v1/warm", json=OK, headers=H)).status == 403
    run(fn, token=None)


@pytest.mark.parametrize("patch", [
    {"copies": 11}, {"copies": -1}, {"copies": True}, {"copies": 1.5}, {"copies": "1"},
    {"ttl_s": 10**400}, {"ttl_s": 86_401}, {"ttl_s": -1}, {"ttl_s": "x"},
    {"start_in_ms": 10**30}, {"start_in_ms": 600_001}, {"start_in_ms": -5},
    {"tool_class": "x" * 129}, {"tool_class": 5}, {"tool_class": ""}, {"reason": ["a"]},
])
def test_out_of_range_fields_are_400_never_500(patch):
    async def fn(svc, c):
        r = await c.post("/v1/warm", json={**OK, **patch}, headers=H)
        assert r.status == 400, await r.text()
        assert all(st.copies == 0 for st in svc.pool.s.values())
    run(fn)


def test_non_object_body_is_400():
    async def fn(svc, c):
        assert (await c.post("/v1/warm", json=[1], headers=H)).status == 400
        assert (await c.post("/v1/warm", data=b"nope", headers=H)).status == 400
    run(fn)


def test_reason_label_is_a_fixed_set():
    async def fn(svc, c):
        for i in range(200):
            await c.post("/v1/warm", json={**OK, "reason": f"junk{i}:x"}, headers=H)
        await c.post("/v1/warm", json={**OK, "reason": "demand"}, headers=H)
        m = await (await c.get("/metrics")).text()
        reasons = {ln.split('reason="')[1].split('"')[0] for ln in m.splitlines()
                   if ln.startswith("skypiea_warm_requests_total{")}
        assert reasons == {"other", "demand"}
    run(fn)


def test_refused_buffer_is_drained():
    async def fn(svc, c):
        for _ in range(300):
            await c.post("/v1/warm", json={**OK, "copies": 10}, headers=H)
        await svc.tick()
        assert len(svc.pool.refused) == 0
        m = await (await c.get("/metrics")).text()
        assert 'skypiea_scale_refused_total{reason="per_tool_cap",tool="shell"}' in m
    run(fn, cfg=PoolConfig(max_per_tool=2))


def test_gateway_controller_client_sends_the_token():
    import httpx

    from gateway.controller_client import HttpControllerClient

    seen = {}

    def handler(req):
        seen.update(req.headers)
        return httpx.Response(200, json={})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            await HttpControllerClient("http://c", 0.5, http, admin_token="tk").warm(
                "shell", 1, 30, "predict:v")
    asyncio.run(go())
    assert seen["x-admin-token"] == "tk"
