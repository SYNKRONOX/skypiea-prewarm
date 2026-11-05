"""Controller: TTL (FR-3), caps, idempotency, min dwell, in-flight floor, restart, failures."""

from __future__ import annotations

import pytest  # noqa: E402

pytest.importorskip("aiohttp", reason="make setup")
pytest.importorskip("prometheus_client", reason="make setup")
pytest.importorskip("kubernetes", reason="make setup")

import asyncio

import pytest

from controller.core import ANN_LAST_USED, ANN_TTL, PoolConfig, WarmPool
from controller.service import ControllerService, FakeScaler

SERVERS = ["user_lookup", "order_lookup", "run_tests"]


class Clock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def pool(**kw) -> WarmPool:
    return WarmPool(SERVERS, PoolConfig(**kw))


def apply(p: WarmPool, now: float, wall: float = 0.0) -> dict[str, int]:
    out = {}
    for a in p.plan(now, wall):
        p.applied(a, now)
        out[a.server] = a.to_replicas
    return out


def test_warm_then_scale_to_zero_at_ttl_plus_purge():
    p = pool(default_ttl_s=30, purge_s=5, min_dwell_s=5)
    p.warm("order_lookup", 1, None, now=0)          # missing ttl -> default 30 s
    assert apply(p, 0) == {"order_lookup": 1}
    for t in (10, 30, 34.9):
        assert apply(p, t) == {}, t                  # still within TTL + purge
    assert apply(p, 35.0) == {"order_lookup": 0}     # removed at TTL + 5 s (FR-3)


def test_later_warm_extends_and_takes_max_copies():
    p = pool(default_ttl_s=30)
    p.warm("run_tests", 1, 10, now=0)
    p.warm("run_tests", 2, 20, now=5)
    assert apply(p, 5) == {"run_tests": 2}
    assert apply(p, 24) == {}                        # 5 + 20 + 5 = 30
    assert apply(p, 30) == {"run_tests": 0}


def test_per_tool_and_cluster_caps_refuse_and_count():
    p = pool(max_per_tool=2, max_total=3, max_scaleups_per_node=0)   # caps only
    assert p.warm("user_lookup", 5, 60, now=0) == 2
    assert ("user_lookup", 3, "per_tool_cap") in p.refused
    assert p.warm("order_lookup", 2, 60, now=0) == 1   # only 1 left in the cluster
    assert ("order_lookup", 1, "cluster_cap") in p.refused
    assert apply(p, 0) == {"user_lookup": 2, "order_lookup": 1}
    assert sum(st.live for st in p.s.values()) <= 3


def test_plan_is_idempotent():
    p = pool()
    p.warm("user_lookup", 1, 60, now=0)
    first = p.plan(0, 0)
    assert len(first) == 1
    assert len(p.plan(0, 0)) == 1      # not applied yet -> same single action, no duplicates
    p.applied(first[0], 0)
    assert p.plan(0.1, 0) == []        # applied -> nothing to do
    p.warm("user_lookup", 1, 60, now=1)
    assert p.plan(1, 0) == []          # same request again -> no change


def test_min_dwell_prevents_flapping():
    p = pool(default_ttl_s=1, purge_s=0, min_dwell_s=5)
    p.warm("user_lookup", 1, 1, now=0)
    assert apply(p, 0) == {"user_lookup": 1}
    assert apply(p, 1) == {}           # expired, but dwell not over
    assert apply(p, 5) == {"user_lookup": 0}
    p.warm("user_lookup", 1, 1, now=5.5)
    assert apply(p, 5.5) == {"user_lookup": 1}   # scale-UP is never delayed


def test_never_below_inflight_and_ttl_counts_from_last_use():
    p = pool(default_ttl_s=0, purge_s=5, min_dwell_s=0)
    p.warm("order_lookup", 1, 0, now=0)
    assert apply(p, 0) == {"order_lookup": 1}
    p.report_inflight("gw1", {"order_lookup": 1}, now=1)
    for t in (6, 20, 40):                         # long call: TTL 0 + 5 s long past
        p.report_inflight("gw1", {"order_lookup": 1}, now=t)  # heartbeat
        assert apply(p, t) == {}, t
    p.report_inflight("gw1", {}, now=41)          # call ended at 41
    assert apply(p, 45) == {}
    assert apply(p, 46) == {"order_lookup": 0}    # 41 + 0 + 5


def test_inflight_alone_scales_up_and_stale_reports_expire():
    p = pool(inflight_stale_s=30, min_dwell_s=0, purge_s=0, default_ttl_s=0)
    p.report_inflight("gw1", {"run_tests": 3}, now=0)
    assert apply(p, 0) == {"run_tests": 1}        # pods serve many calls: 1, not 3
    assert apply(p, 31) == {"run_tests": 0}       # gateway vanished: report went stale


def test_rebuild_after_restart_keeps_warm_pods_until_their_ttl():
    p = pool(default_ttl_s=30, purge_s=5, min_dwell_s=5)
    wall = 2_000_000.0
    # deployment was warmed 10 s ago with TTL 30 -> 20 s left
    p.rebuild("run_tests", 1, 1, {ANN_LAST_USED: str(wall - 10), ANN_TTL: "30"}, wall, now=0)
    p.rebuild("user_lookup", 0, 0, {}, wall, now=0)
    assert apply(p, 0) == {}
    assert apply(p, 24) == {}
    assert apply(p, 25) == {"run_tests": 0}       # 20 s left + 5 s purge


def test_rebuild_without_annotation_never_kills_blind():
    p = pool(default_ttl_s=30, purge_s=5, min_dwell_s=0)
    p.rebuild("order_lookup", 2, 2, {}, 5_000.0, now=0)   # scaled by hand / old controller
    assert apply(p, 34) == {}
    assert apply(p, 35) == {"order_lookup": 0}


def test_pending_stuck_detected():
    p = pool(pending_warn_s=120)
    p.observe("run_tests", 1, 0, now=0)
    assert p.stuck(100) == []
    assert p.stuck(121) == ["run_tests"]
    p.observe("run_tests", 1, 1, now=130)
    assert p.stuck(131) == []


def test_always_warm_baseline():
    p = pool(always_warm=True, max_scaleups_per_node=0)
    assert apply(p, 0) == {s: 1 for s in SERVERS}
    assert apply(p, 1000) == {}


def ready(p: WarmPool, server: str, now: float) -> None:
    p.observe(server, p.s[server].live, p.s[server].live, now)


def test_scaleups_limited_per_node_and_queued_by_due_time():
    p = pool(max_scaleups_per_node=2, nodes=1)
    p.warm("run_tests", 1, 60, now=0, start_in_ms=900)
    p.warm("user_lookup", 1, 60, now=0, start_in_ms=100)
    p.warm("order_lookup", 1, 60, now=0, start_in_ms=500)
    # 2 slots: the two that are due first start, the third waits (not dropped)
    assert apply(p, 0) == {"user_lookup": 1, "order_lookup": 1}
    assert p.queue == [("run_tests", 1)] and p.queued == ["run_tests"]
    assert apply(p, 1) == {}                        # both still starting: no free slot
    ready(p, "user_lookup", 2)                      # one became Ready -> slot frees
    assert apply(p, 2) == {"run_tests": 1}
    assert p.queue == [] and p.dequeued == [("run_tests", 2)]


def test_limit_scales_with_nodes_and_zero_means_unlimited():
    for kw, n in (({"max_scaleups_per_node": 2, "nodes": 2}, 3),
                  ({"max_scaleups_per_node": 0}, 3), ({"max_scaleups_per_node": 1}, 1)):
        p = pool(**kw)
        for s in SERVERS:
            p.warm(s, 1, 60, now=0)
        assert len(apply(p, 0)) == n


def test_waiting_call_goes_first_on_a_tie_and_expired_wish_is_counted():
    p = pool(max_scaleups_per_node=1, default_ttl_s=5, purge_s=1)
    p.warm("order_lookup", 1, 5, now=0)
    p.warm("user_lookup", 1, 5, now=0)
    p.report_inflight("gw", {"user_lookup": 1}, now=0)   # a call is waiting on it
    assert apply(p, 0) == {"user_lookup": 1}
    assert p.queue == [("order_lookup", 1)]
    # order_lookup's warm (ttl 5 + purge 1) ends before any slot frees: counted, not silent
    assert apply(p, 7) == {}
    assert p.expired == [("order_lookup", 7)] and p.queue == []


def test_stuck_pod_does_not_hold_a_slot_forever():
    p = pool(max_scaleups_per_node=1, pending_warn_s=120)
    p.warm("run_tests", 1, 600, now=0)
    assert apply(p, 0) == {"run_tests": 1}
    p.observe("run_tests", 1, 0, now=0)              # never becomes Ready
    p.warm("user_lookup", 1, 600, now=1)
    assert apply(p, 1) == {}
    assert apply(p, 121) == {"user_lookup": 1}       # run_tests is stuck -> slot released


def test_service_exposes_queue_metrics():
    async def go():
        clock = Clock(0)
        svc = ControllerService(FakeScaler(), PoolConfig(max_scaleups_per_node=1),
                                clock=clock, wall=lambda: 0.0)
        svc.warm("user_lookup", 1, 60, "predict:markov1", start_in_ms=0)
        svc.warm("order_lookup", 1, 60, "predict:markov1", start_in_ms=200)
        await svc.tick()
        from prometheus_client import generate_latest
        text = generate_latest(svc.metrics.registry).decode()
        assert "skypiea_scaleup_queue_depth 1.0" in text
        assert 'skypiea_scaleups_queued_total{tool="order_lookup"} 1.0' in text
        svc.pool.observe("user_lookup", 1, 1, clock())
        await svc.tick()
        text = generate_latest(svc.metrics.registry).decode()
        assert "skypiea_scaleup_queue_depth 0.0" in text
        assert "skypiea_scaleup_queue_wait_seconds_count 1.0" in text
    asyncio.run(go())


# ---------------- service: HTTP API, scaler failures, restart mid-scale ----------------

def test_service_scale_failure_is_retried_next_tick():
    async def go():
        clock = Clock(0)
        scaler = FakeScaler(fail_times=1)
        svc = ControllerService(scaler, PoolConfig(), clock=clock, wall=lambda: 0.0)
        svc.warm("order_lookup", 1, 60, "test")
        assert await svc.tick() == []                    # injected failure
        assert scaler.calls == []
        assert svc.metrics.errors.labels("order_lookup")._value.get() == 1
        clock.t = 0.25
        done = await svc.tick()                          # retried
        assert [(a.server, a.to_replicas) for a in done] == [("order_lookup", 1)]
        assert scaler.calls[0][2][ANN_TTL] == "60"
    asyncio.run(go())


def test_service_restart_mid_scale_converges_without_duplicates():
    """The first controller patched replicas=2 (with annotations) and died before recording
    it. A new controller rebuilds from the Deployment and must not scale again."""
    async def go():
        wall = 3_000_000.0
        clock = Clock(0)
        s1 = FakeScaler()
        c1 = ControllerService(s1, PoolConfig(), clock=clock, wall=lambda: wall)
        c1.warm("run_tests", 2, 60, "predict:x")
        await c1.tick()
        server, replicas, ann = s1.calls[-1]
        # --- crash; the Deployment now has replicas=2 + ann; new process starts ---
        s2 = FakeScaler()
        c2 = ControllerService(s2, PoolConfig(), clock=Clock(0), wall=lambda: wall + 3)
        c2.pool.rebuild(server, replicas, 0, ann, wall + 3, 0)
        assert await c2.tick() == []                     # nothing to do: converged
        assert c2.pool.s["run_tests"].warm_until == pytest.approx(57.0)
    asyncio.run(go())


def test_http_api_validates_and_maps_classes():
    from aiohttp.test_utils import TestClient, TestServer

    async def go():
        svc = ControllerService(FakeScaler(), PoolConfig(max_per_tool=2), clock=Clock(0))
        async with TestClient(TestServer(svc.web_app())) as c:
            r = await c.post("/v1/warm", json={"tool_class": "write_file", "copies": 5,
                                               "ttl_s": None, "reason": "predict:v1"})
            body = await r.json()
            assert r.status == 200 and body == {"server": "edit_file", "accepted": 2,
                                                "requested": 5}
            r = await c.post("/v1/warm", json={"tool_class": "x", "copies": -1})
            assert r.status == 400
            r = await c.post("/v1/warm", data=b"not json")
            assert r.status == 400
            r = await c.post("/v1/inflight", json={"gateway_id": "g", "counts": {"shell": 2,
                                                                                "nope": 1}})
            assert r.status == 200
            assert (await (await c.get("/v1/state")).json())["shell"]["inflight"] == 2
            assert (await c.get("/healthz")).status == 503   # not rebuilt yet
            m = await (await c.get("/metrics")).text()
            assert 'skypiea_scale_refused_total{reason="per_tool_cap",tool="edit_file"} 3.0' in m
    asyncio.run(go())
