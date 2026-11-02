"""Controller runtime: HTTP API + reconcile loop around the pure WarmPool.

HTTP (aiohttp, cluster-internal Service only):
  POST /v1/warm      {tool_class, copies, ttl_s|null, reason}     -> {accepted, server}
  POST /v1/inflight  {gateway_id, counts: {server: n}}
  GET  /v1/state     current pool state (debugging, functional checks)
  GET  /healthz, GET /metrics
The Scaler does the Kubernetes work (K8sScaler) or records it (FakeScaler, tests).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Protocol

from aiohttp import web
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    generate_latest,
)

from controller.core import Action, PoolConfig, WarmPool
from tools.common.toolmap import ToolMap, k8s_name, load

log = logging.getLogger("skypiea.controller")


class Scaler(Protocol):
    async def scale(self, server: str, replicas: int, annotations: dict[str, str]) -> None: ...


class FakeScaler:
    def __init__(self, fail_times: int = 0):
        self.calls: list[tuple[str, int, dict[str, str]]] = []
        self.fail_times = fail_times

    async def scale(self, server: str, replicas: int, annotations: dict[str, str]) -> None:
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("injected scale failure")
        self.calls.append((server, replicas, dict(annotations)))


class K8sScaler:
    """One PATCH per change, carrying replicas AND the warm-state annotations, so a restart
    mid-scale never sees replicas without the matching annotations (or the reverse)."""

    def __init__(self, namespace: str):
        from kubernetes import client, config

        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()
        self.apps, self.ns = client.AppsV1Api(), namespace

    async def scale(self, server: str, replicas: int, annotations: dict[str, str]) -> None:
        body = {"spec": {"replicas": replicas}}
        if annotations:
            body["metadata"] = {"annotations": annotations}
        await asyncio.to_thread(self.apps.patch_namespaced_deployment, k8s_name(server),
                                self.ns, body)


class Metrics:
    def __init__(self) -> None:
        r = self.registry = CollectorRegistry()
        self.idle = Counter("skypiea_pod_seconds_idle_total",
                            "Ready pod-seconds with no call in flight", ["tool"], registry=r)
        self.events = Counter("skypiea_scale_events_total", "Replica changes applied",
                              ["tool", "dir", "reason"], registry=r)
        self.pool = Gauge("skypiea_warm_pool_size", "Replicas (spec) per tool", ["tool"],
                          registry=r)
        self.refused = Counter("skypiea_scale_refused_total", "Replicas refused by a cap",
                               ["tool", "reason"], registry=r)
        self.errors = Counter("skypiea_scale_errors_total", "Failed scale patches", ["tool"],
                              registry=r)
        self.stuck = Gauge("skypiea_pending_stuck", "1 if replicas > 0 but none Ready for too "
                           "long", ["tool"], registry=r)
        self.warm_requests = Counter("skypiea_warm_requests_total", "POST /v1/warm",
                                     ["tool", "reason"], registry=r)


class ControllerService:
    def __init__(self, scaler: Scaler, cfg: PoolConfig | None = None,
                 tool_map: ToolMap | None = None, clock=time.monotonic, wall=time.time,
                 tick_s: float = 0.25):
        self.tm = tool_map or load()
        self.pool = WarmPool(list(self.tm.servers), cfg)
        self.scaler, self.clock, self.wall, self.tick_s = scaler, clock, wall, tick_s
        self.metrics = Metrics()
        self._last_tick = clock()
        self._lock = asyncio.Lock()
        self.ready_event = asyncio.Event()  # set once live state has been loaded (rebuild)

    def server_for(self, tool_class: str) -> str:
        return self.tm.server_for(tool_class) or (
            tool_class if tool_class in self.tm.servers else self.tm.fallback)

    # ---------------- inputs ----------------
    def warm(self, tool_class: str, copies: int, ttl_s: int | None, reason: str) -> dict:
        server = self.server_for(tool_class)
        n_ref = len(self.pool.refused)
        accepted = self.pool.warm(server, copies, ttl_s, self.clock())
        self._count_refused(n_ref)
        self.metrics.warm_requests.labels(server, reason.split(":")[0]).inc()
        return {"server": server, "accepted": accepted, "requested": copies}

    def _count_refused(self, n_before: int) -> None:
        for server, n, why in self.pool.refused[n_before:]:
            self.metrics.refused.labels(server, why).inc(n)

    # ---------------- reconcile ----------------
    async def tick(self) -> list[Action]:
        async with self._lock:
            now = self.clock()
            dt, self._last_tick = now - self._last_tick, now
            for name in self.pool.s:
                self.metrics.idle.labels(name).inc(self.pool.idle_pods(name, now) * dt)
            n_ref = len(self.pool.refused)
            actions = self.pool.plan(now, self.wall())
            self._count_refused(n_ref)
            done = []
            for a in actions:
                try:
                    await self.scaler.scale(a.server, a.to_replicas, a.annotations)
                except Exception as e:  # retried on the next tick (plan is idempotent)
                    self.metrics.errors.labels(a.server).inc()
                    log.warning("scale %s -> %d failed: %s", a.server, a.to_replicas, e)
                    continue
                self.pool.applied(a, self.clock())
                self.metrics.events.labels(a.server, a.direction, a.reason).inc()
                log.info("scaled %s %d -> %d (%s)", a.server, a.from_replicas, a.to_replicas,
                         a.reason)
                done.append(a)
            stuck = set(self.pool.stuck(now))
            for name, st in self.pool.s.items():
                self.metrics.pool.labels(name).set(st.live)
                self.metrics.stuck.labels(name).set(1 if name in stuck else 0)
            return done

    async def run(self) -> None:
        await self.ready_event.wait()
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("reconcile tick failed")
            await asyncio.sleep(self.tick_s)

    # ---------------- HTTP ----------------
    def state(self) -> dict:
        now = self.clock()
        return {name: {"live": st.live, "ready": st.ready, "copies": st.copies,
                       "warm_for_s": round(max(0.0, st.warm_until - now), 2),
                       "inflight": self.pool.inflight(name, now)}
                for name, st in self.pool.s.items()}

    def web_app(self) -> web.Application:
        async def warm(request: web.Request) -> web.Response:
            try:
                d = await request.json()
                cls, copies = str(d["tool_class"]), int(d.get("copies", 1))
                ttl = d.get("ttl_s")
                ttl = None if ttl is None else int(ttl)
                reason = str(d.get("reason", ""))[:64] or "unspecified"
            except (ValueError, KeyError, TypeError) as e:
                return web.json_response({"error": f"bad request: {e}"}, status=400)
            if copies < 0 or (ttl is not None and ttl < 0):
                return web.json_response({"error": "copies and ttl_s must be >= 0"}, status=400)
            return web.json_response(self.warm(cls, copies, ttl, reason))

        async def inflight(request: web.Request) -> web.Response:
            try:
                d = await request.json()
                counts = {str(k): int(v) for k, v in dict(d["counts"]).items()}
                gid = str(d["gateway_id"])
            except (ValueError, KeyError, TypeError) as e:
                return web.json_response({"error": f"bad request: {e}"}, status=400)
            self.pool.report_inflight(gid, {k: v for k, v in counts.items()
                                            if k in self.pool.s}, self.clock())
            return web.json_response({"ok": True})

        async def state(_: web.Request) -> web.Response:
            return web.json_response(self.state())

        async def healthz(_: web.Request) -> web.Response:
            ok = self.ready_event.is_set()
            return web.json_response({"status": "ok" if ok else "starting"},
                                     status=200 if ok else 503)

        async def metrics(_: web.Request) -> web.Response:
            return web.Response(body=generate_latest(self.metrics.registry),
                                headers={"Content-Type": CONTENT_TYPE_LATEST})

        app = web.Application(client_max_size=64 * 1024)
        app.router.add_post("/v1/warm", warm)
        app.router.add_post("/v1/inflight", inflight)
        app.router.add_get("/v1/state", state)
        app.router.add_get("/healthz", healthz)
        app.router.add_get("/metrics", metrics)
        return app
