"""Skypiea gateway: one MCP endpoint (POST /mcp) in front of all tool servers.

Per tools/call:
  1. Session from the Mcp-Session-Id header (created if absent, echoed back).
  2. params.name -> tool class -> server Service (tools/tool_map.yaml).
  3. If the server has 0 ready pods: ask the controller for one, HOLD up to hold_ms (FR-9) for a
     pod that is about to be Ready; if none, fall through to the cold path (wait for Ready, up
     to cold_timeout_s) and mark the call cold.
  4. Forward the JSON-RPC request (params.name rewritten to the server's tool), return its reply.
  5. Record the call in the session history, then ask the predictor OFF the critical path
     (fire-and-forget, with a timeout) and pass warm decisions to the controller.
FR-4: a dead, slow or off-contract predictor is counted and ignored; calls never fail because of
it, and the gateway then behaves as plain scale-to-zero.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route as HttpRoute

from gateway.contract import ContractError, DecideRequest, HistoryItem, OracleHint
from gateway.controller_client import ControllerClient
from gateway.predictor import PredictorClient
from gateway.readiness import ReadyTracker
from gateway.routing import Router
from gateway.sessions import SessionStore

log = logging.getLogger("skypiea.gateway")
SESSION_HEADER = "Mcp-Session-Id"
ORACLE_HEADER = "X-Skypiea-Oracle"
PASS_HEADERS = ("content-type", "accept", "mcp-protocol-version")


@dataclass
class GatewayConfig:
    namespace: str = "skypiea-tools"
    hold_ms: float = 150.0
    cold_timeout_s: float = 120.0
    upstream_timeout_s: float = 300.0
    predictor_timeout_s: float = 0.3
    demand_ttl_s: int | None = 0  # TTL for a pod started by a call itself (0 = scale-to-zero)
    session_idle_s: float = 600.0
    purge_interval_s: float = 10.0
    inflight_report_s: float = 1.0
    gateway_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])


class Metrics:
    def __init__(self) -> None:
        r = self.registry = CollectorRegistry()
        self.calls = Counter("skypiea_gateway_calls_total", "Tool calls", ["tool", "cold"],
                             registry=r)
        self.added = Histogram("skypiea_gateway_added_latency_seconds",
                               "Gateway time on top of the tool's own response time "
                               "(hold + cold wait + proxying)", ["tool"], registry=r,
                               buckets=(.001, .005, .01, .025, .05, .1, .15, .25, .5, 1, 2.5, 5,
                                        10, 30, 60, 120))
        self.hold = Histogram("skypiea_gateway_hold_ms", "Time a call was held waiting for a "
                              "warm pod (FR-9)", ["tool"], registry=r,
                              buckets=(0, 1, 5, 10, 25, 50, 75, 100, 125, 150, 200))
        self.hits = Counter("skypiea_gateway_prewarm_hits_total",
                            "Calls that found a ready pod on arrival", ["tool"], registry=r)
        self.misses = Counter("skypiea_gateway_prewarm_misses_total",
                              "Calls that found no ready pod on arrival", ["tool"], registry=r)
        self.pred_latency = Histogram("skypiea_gateway_predictor_latency_seconds",
                                      "Predictor round trip", registry=r,
                                      buckets=(.001, .005, .01, .025, .05, .1, .2, .3, .5, 1))
        self.pred_errors = Counter("skypiea_gateway_predictor_errors_total",
                                   "Predictor failures (ignored, FR-4)", ["reason"], registry=r)
        self.ctrl_errors = Counter("skypiea_gateway_controller_errors_total",
                                   "Controller request failures", ["op"], registry=r)
        self.not_ready = Counter("skypiea_gateway_not_ready_total",
                                 "Calls failed because no pod became Ready in time", ["tool"],
                                 registry=r)
        self.sessions = Gauge("skypiea_gateway_sessions", "Live sessions", registry=r)


def _rpc_error(id_: Any, code: int, msg: str, status: int = 200) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": msg}},
                        status_code=status)


class Gateway:
    def __init__(self, cfg: GatewayConfig, router: Router, predictor: PredictorClient | None,
                 controller: ControllerClient, tracker: ReadyTracker,
                 http: httpx.AsyncClient | None = None, clock=time.monotonic):
        self.cfg, self.router, self.predictor = cfg, router, predictor
        self.controller, self.tracker, self.clock = controller, tracker, clock
        self.http = http or httpx.AsyncClient()
        self.sessions = SessionStore(cfg.session_idle_s, clock)
        self.metrics = Metrics()
        self.inflight: dict[str, int] = {}
        self._tasks: set[asyncio.Task] = set()
        self._inflight_dirty = asyncio.Event()

    # ---------------- background work ----------------
    def _spawn(self, coro) -> asyncio.Task:
        t = asyncio.create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)
        return t

    async def drain(self) -> None:
        """Wait for background predictor/controller tasks (tests)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def _ctrl(self, op: str, coro) -> None:
        try:
            await coro
        except Exception as e:
            self.metrics.ctrl_errors.labels(op).inc()
            log.warning("controller %s failed: %s", op, e)

    async def purge_loop(self) -> None:
        while True:
            await asyncio.sleep(self.cfg.purge_interval_s)
            self.sessions.purge()
            self.metrics.sessions.set(len(self.sessions))

    async def inflight_loop(self) -> None:
        """Report in-flight counts on change, and as a heartbeat every inflight_report_s."""
        while True:
            try:
                await asyncio.wait_for(self._inflight_dirty.wait(), self.cfg.inflight_report_s)
            except TimeoutError:
                pass
            self._inflight_dirty.clear()
            await self._ctrl("inflight", self.controller.inflight(
                self.cfg.gateway_id, {k: v for k, v in self.inflight.items() if v}))

    def _inflight_delta(self, server: str, d: int) -> None:
        self.inflight[server] = self.inflight.get(server, 0) + d
        self._inflight_dirty.set()

    # ---------------- predictor (off the critical path) ----------------
    async def _predict(self, sid: str, req: DecideRequest) -> None:
        if self.predictor is None:
            return
        t0 = self.clock()
        try:
            resp = await asyncio.wait_for(self.predictor.decide(req), self.cfg.predictor_timeout_s)
        except TimeoutError:
            self.metrics.pred_errors.labels("timeout").inc()
            return
        except ContractError as e:
            self.metrics.pred_errors.labels("contract").inc()
            log.warning("predictor answer off-contract: %s", e)
            return
        except (httpx.HTTPError, ValueError, OSError) as e:
            self.metrics.pred_errors.labels(type(e).__name__).inc()
            return
        except Exception as e:  # never let the predictor hurt the gateway
            self.metrics.pred_errors.labels("other").inc()
            log.warning("predictor failed: %r", e)
            return
        self.metrics.pred_latency.observe(self.clock() - t0)
        for d in resp.decisions:
            if d.warm and d.copies > 0:
                self._spawn(self._warm_later(d.tool_class, d.copies, d.ttl_s, d.start_in_ms,
                                             f"predict:{resp.model_version}"))

    async def _warm_later(self, cls: str, copies: int, ttl_s: int | None, start_in_ms: int,
                          reason: str) -> None:
        if start_in_ms > 0:
            await asyncio.sleep(start_in_ms / 1000.0)
        await self._ctrl("warm", self.controller.warm(cls, copies, ttl_s, reason))

    # ---------------- the call path ----------------
    async def handle_call(self, body: dict[str, Any], sid: str,
                          headers: dict[str, str] | None = None) -> tuple[dict[str, Any], dict]:
        params = body.get("params") or {}
        name = params.get("name")
        if not isinstance(name, str):
            return ({"jsonrpc": "2.0", "id": body.get("id"),
                     "error": {"code": -32602, "message": "params.name is required"}}, {})
        route = self.router.resolve(name)
        session = self.sessions.get(sid)
        t_arrive = self.clock()
        batch = session.start_call()
        self._inflight_delta(route.server, +1)
        cold, held_ms, info = False, 0.0, {}
        try:
            if self.tracker.ready(route.server) > 0:
                self.metrics.hits.labels(route.tool_class).inc()
            else:
                self.metrics.misses.labels(route.tool_class).inc()
                self._spawn(self._ctrl("warm", self.controller.warm(
                    route.tool_class, 1, self.cfg.demand_ttl_s, "demand")))
                ok = await self.tracker.wait_ready(route.server, self.cfg.hold_ms / 1000.0)
                held_ms = (self.clock() - t_arrive) * 1000.0
                self.metrics.hold.labels(route.tool_class).observe(min(held_ms,
                                                                       self.cfg.hold_ms))
                if not ok:
                    cold = True
                    ok = await self.tracker.wait_ready(route.server, self.cfg.cold_timeout_s)
                if not ok:
                    self.metrics.not_ready.labels(route.tool_class).inc()
                    return ({"jsonrpc": "2.0", "id": body.get("id"), "error": {
                        "code": -32001, "message": f"tool {route.server} not ready within "
                        f"{self.cfg.cold_timeout_s:.0f}s"}}, {"cold": cold})
            fwd = dict(body)
            fwd["params"] = {**params, "name": route.server}
            # 2026-07-28: Mcp-Method / Mcp-Name must match the (rewritten) body
            headers = {**(headers or {}), "Mcp-Method": "tools/call", "Mcp-Name": route.server}
            t_up = self.clock()
            result = await self._forward(route, fwd, headers or {})
            upstream_s = self.clock() - t_up
        finally:
            session.inflight -= 1
            self._inflight_delta(route.server, -1)
        t_end = self.clock()
        self.metrics.calls.labels(route.tool_class, str(cold).lower()).inc()
        self.metrics.added.labels(route.tool_class).observe(max(0.0, t_end - t_arrive
                                                                - upstream_s))
        is_err = "error" in result or bool((result.get("result") or {}).get("isError"))
        session.history.append(HistoryItem(route.tool_class, (t_arrive - session.created) * 1e3,
                                           (t_end - session.created) * 1e3, is_err, batch))
        req = DecideRequest(sid, (t_end - session.created) * 1e3, list(session.history),
                            list(session.oracle))
        self._spawn(self._predict(sid, req))
        info = {"cold": cold, "held_ms": round(held_ms, 1), "tool_class": route.tool_class,
                "server": route.server}
        return result, info

    async def _forward(self, route, fwd: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        for attempt in range(2):
            try:
                r = await self.http.post(route.url, json=fwd, timeout=self.cfg.upstream_timeout_s,
                                         headers=headers)
                return r.json()
            except (httpx.ConnectError, httpx.RemoteProtocolError):
                # the pod we were routed to went away between Ready and connect: wait, retry
                if attempt == 0 and await self.tracker.wait_ready(route.server,
                                                                  self.cfg.cold_timeout_s):
                    continue
                raise
        raise RuntimeError("unreachable")

    # ---------------- HTTP surface ----------------
    def tools_list(self) -> dict[str, Any]:
        tools = []
        for name, spec in self.router.tm.servers.items():
            for n in dict.fromkeys([name, *spec.classes]):
                tools.append({"name": n, "description": f"Skypiea tool (server {name})",
                              "inputSchema": {"type": "object"}})
        # 2026-07-28 result envelope (same fields the SDK's servers send)
        return {"tools": tools, "resultType": "complete", "cacheScope": "private", "ttlMs": 0,
                "_meta": {"io.modelcontextprotocol/serverInfo": {"name": "skypiea-gateway",
                                                                  "version": "0.1.0"}}}

    async def mcp(self, request: Request) -> Response:
        sid = request.headers.get(SESSION_HEADER) or uuid.uuid4().hex
        hdr = {SESSION_HEADER: sid}
        if request.method == "DELETE":
            self.sessions.end(sid)
            return Response(status_code=204, headers=hdr)
        try:
            body = json.loads(await request.body())
        except json.JSONDecodeError:
            return _rpc_error(None, -32700, "parse error", 400)
        if not isinstance(body, dict):
            return _rpc_error(None, -32600, "batch requests are not supported", 400)
        if "id" not in body:  # notification
            return Response(status_code=202, headers=hdr)
        if oracle := request.headers.get(ORACLE_HEADER):
            try:
                self.sessions.get(sid).oracle = [OracleHint(str(o["tool_class"]), float(o["in_ms"]))
                                                 for o in json.loads(oracle)]
            except (ValueError, KeyError, TypeError):
                pass
        method = body.get("method")
        if method == "tools/call":
            fwd_headers = {k: v for k, v in request.headers.items()
                           if k.lower() in PASS_HEADERS}
            try:
                result, info = await self.handle_call(body, sid, fwd_headers)
            except (httpx.HTTPError, ValueError) as e:
                return _rpc_error(body.get("id"), -32002, f"tool call failed: {e}")
            if info:
                hdr["X-Skypiea-Cold"] = "1" if info.get("cold") else "0"
                if "held_ms" in info:
                    hdr["X-Skypiea-Held-Ms"] = str(info["held_ms"])
            return JSONResponse(result, headers=hdr)
        if method == "tools/list":
            return JSONResponse({"jsonrpc": "2.0", "id": body["id"], "result": self.tools_list()},
                                headers=hdr)
        if method == "ping":
            return JSONResponse({"jsonrpc": "2.0", "id": body["id"],
                                 "result": {"resultType": "complete"}}, headers=hdr)
        return _rpc_error(body.get("id"), -32601, f"method not supported: {method}")

    async def healthz(self, _: Request) -> Response:
        return JSONResponse({"status": "ok", "sessions": len(self.sessions)})

    async def metrics_ep(self, _: Request) -> Response:
        self.metrics.sessions.set(len(self.sessions))
        return Response(generate_latest(self.metrics.registry), media_type=CONTENT_TYPE_LATEST)

    def app(self, background: bool = True) -> Starlette:
        @contextlib.asynccontextmanager
        async def lifespan(_app):
            tasks = []
            if background:
                tasks = [asyncio.create_task(self.purge_loop()),
                         asyncio.create_task(self.inflight_loop())]
            yield
            for t in tasks:
                t.cancel()

        return Starlette(routes=[
            HttpRoute("/mcp", self.mcp, methods=["POST", "DELETE"]),
            HttpRoute("/healthz", self.healthz, methods=["GET"]),
            HttpRoute("/metrics", self.metrics_ep, methods=["GET"]),
        ], lifespan=lifespan)
