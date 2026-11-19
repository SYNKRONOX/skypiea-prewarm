"""Issue #20: every Kubernetes call has a timeout, a hung patch does not freeze the scaling loop,
and /healthz reports a stuck loop (503) so the livenessProbe restarts the controller."""

from __future__ import annotations

import asyncio

from controller.core import PoolConfig
from controller.service import ControllerService, K8sScaler


class Clock:
    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t


class HangingScaler:
    def __init__(self):
        self.calls = 0

    async def scale(self, server, replicas, annotations):
        self.calls += 1
        await asyncio.sleep(3600)


def test_hung_patch_times_out_and_the_loop_goes_on():
    async def go():
        sc = HangingScaler()
        svc = ControllerService(sc, PoolConfig(), clock=Clock(0), admin_token="t",
                                scale_timeout_s=0.05)
        svc.warm("shell", 1, 30, "predict:v", 0)
        await asyncio.wait_for(svc.tick(), 2)         # returns although the patch hangs
        assert svc.metrics.errors.labels("shell")._value.get() == 1
        await asyncio.wait_for(svc.tick(), 2)         # and the next tick runs too
        assert sc.calls == 2
    asyncio.run(go())


def test_healthz_reports_a_stuck_loop():
    from aiohttp.test_utils import TestClient, TestServer

    async def go():
        clock = Clock(0)
        svc = ControllerService(HangingScaler(), PoolConfig(), clock=clock, admin_token="t",
                                stuck_after_s=30)
        svc.ready_event.set()
        async with TestClient(TestServer(svc.web_app())) as c:
            await svc.tick()
            assert (await c.get("/healthz")).status == 200
            clock.t = 31                                # no tick finished for 31 s
            r = await c.get("/healthz")
            assert r.status == 503 and (await r.json())["status"] == "stuck"
            await svc.tick()
            assert (await c.get("/healthz")).status == 200
    asyncio.run(go())


def test_k8s_scaler_passes_a_request_timeout():
    seen = {}

    class Apps:
        def patch_namespaced_deployment(self, name, ns, body, **kw):
            seen.update(kw)

    s = K8sScaler.__new__(K8sScaler)
    s.apps, s.ns, s.timeout_s = Apps(), "ns", 7.0
    asyncio.run(s.scale("shell", 1, {}))
    assert seen["_request_timeout"] == 7.0


def test_chart_controller_has_a_liveness_probe():
    import shutil
    import subprocess
    from pathlib import Path

    import pytest
    yaml = pytest.importorskip("yaml")
    if shutil.which("helm") is None:
        pytest.skip("needs helm")
    chart = Path(__file__).resolve().parents[1] / "charts" / "skypiea"
    docs = [d for d in yaml.safe_load_all(subprocess.run(
        ["helm", "template", "x", str(chart)], check=True, capture_output=True,
        text=True).stdout) if d]
    ctl = next(d for d in docs if d["kind"] == "Deployment"
               and d["metadata"]["name"] == "skypiea-controller")
    probe = ctl["spec"]["template"]["spec"]["containers"][0]["livenessProbe"]
    assert probe["httpGet"]["path"] == "/healthz"
