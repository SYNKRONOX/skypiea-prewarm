"""Skypiea predictor service: ``POST /decide`` (the gateway contract), reload, health, metrics.

    COLD_START_PATH=... MODEL_PATH=models/<version> python -m services.predictor

Environment:
- ``MODEL_PATH``: the bundle folder served at startup (``services/predictor/engine.py``).
- ``COLD_START_PATH``: the infra cold-start file (read once at startup, by path).
- ``ADMIN_TOKEN``: required in ``X-Admin-Token`` for /reload and /rollback (constant-time
  compare); unset disables both (403).
- ``MODEL_ROOT``: local bundles may be reloaded only from folders under it (after realpath).
- ``WORKERS``: uvicorn worker processes (default 1). With more than one, /reload and
  /rollback answer 409 (each worker holds its own model; a reload would reach one of them)
  and /metrics covers the worker that answers the scrape; scale with replicas instead, or
  roll the pods to change the model.
- ``MODEL_URI_ALLOWLIST``: comma-separated ``s3://<bucket>/<prefix>`` entries that may be
  reloaded from (needs boto3 in the image; otherwise refused).

Behaviour:
- ``/decide`` answers HTTP 200 for every JSON body under the limits, also when the request is
  unusable or the model fails: then ``decisions: []``, ``mode: "none"``, ``degraded: true``
  (the gateway's cold path, FR-4). Malformed JSON -> 400; body over 256 KB or more than 1,000
  history items -> 413.
- Stateless (FR-7, NFR-6): nothing per session is kept, logged, written or used as a label.
- A request uses the bundle that was current when it started; reload builds and verifies a
  new bundle off to the side and swaps one reference, so in-flight requests never fail.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
import math
import os
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from predictors.cold_start import ColdStarts
from services.predictor.engine import (
    Bundle,
    BundleError,
    answer,
    empty_answer,
    load_bundle,
    read_folder,
    warmup_p99_ms,
)
from services.predictor.schema import MAX_BODY_BYTES, TooLarge, parse_request

log = logging.getLogger("skypiea.predictor")
FLAG_NAMES = ("now_ms", "history_total", "item", "tool_class", "t_start_ms", "t_end_ms",
              "error", "batch_id")
BAD_REASONS = ("not_object", "session_id", "history")
RELOAD_P99_MS = 50.0


class Metrics:
    def __init__(self) -> None:
        self.registry = r = CollectorRegistry()
        self.requests = Counter("skypiea_predictor_requests_total", "/decide requests",
                                ["outcome"], registry=r)
        self.latency = Histogram("skypiea_predictor_latency_seconds", "/decide compute time",
                                 buckets=(.001, .0025, .005, .01, .02, .03, .04, .05, .075, .1,
                                          .25, .5, 1.0), registry=r)
        self.degraded = Counter("skypiea_predictor_degraded_total", "degraded answers",
                                ["reason"], registry=r)
        self.repaired = Counter("skypiea_predictor_input_repaired_total",
                                "request fields repaired or ignored", ["field"], registry=r)
        self.reloads = Counter("skypiea_predictor_reloads_total", "reload/rollback attempts",
                               ["outcome"], registry=r)
        self.version = Gauge("skypiea_predictor_model_info", "1 for the served model version",
                             ["model_version"], registry=r)


@dataclass
class Settings:
    model_path: str | None
    cold_start_path: str | None
    admin_token: str | None
    model_root: str | None
    s3_allowlist: tuple[str, ...]
    workers: int = 1

    @classmethod
    def from_env(cls) -> Settings:
        allow = tuple(x.strip() for x in os.environ.get("MODEL_URI_ALLOWLIST", "").split(",")
                      if x.strip())
        return cls(os.environ.get("MODEL_PATH"), os.environ.get("COLD_START_PATH"),
                   os.environ.get("ADMIN_TOKEN") or None, os.environ.get("MODEL_ROOT"), allow,
                   int(os.environ.get("WORKERS", "1")))


class State:
    def __init__(self, settings: Settings, metrics: Metrics) -> None:
        self.settings, self.metrics = settings, metrics
        self.cold: ColdStarts | None = None
        self.current: Bundle | None = None
        self.previous: Bundle | None = None
        self.reload_lock = asyncio.Lock()
        self.degraded_count = 0

    def serve(self, b: Bundle) -> None:
        self.previous, self.current = self.current, b
        self.metrics.version.clear()
        self.metrics.version.labels(b.version).set(1)


# --------------------------------------------------------------------------- model URIs
class Refused(ValueError):
    """A reload request that is not allowed; the message is safe to return."""


def resolve_local(uri: str, root: str | None) -> Path:
    if root is None:
        raise Refused("local reload disabled (MODEL_ROOT unset)")
    if "://" in uri or uri.startswith("file:"):
        raise Refused("only plain paths under MODEL_ROOT or allowlisted s3:// URIs")
    base = Path(os.path.realpath(root))
    p = Path(uri)
    real = Path(os.path.realpath(p if p.is_absolute() else base / p))
    if os.path.commonpath([base, real]) != str(base) or real == base:
        raise Refused("path is outside MODEL_ROOT")
    if not real.is_dir():
        raise Refused("no such model folder")
    return real


def fetch(uri: str, s: Settings) -> dict[str, bytes]:
    if uri.startswith("s3://"):
        if not any(uri.rstrip("/").startswith(a.rstrip("/") + "/") or uri.rstrip("/") ==
                   a.rstrip("/") for a in s.s3_allowlist) or ".." in uri:
            raise Refused("s3 URI not on the allowlist")
        return fetch_s3(uri)
    return read_folder(resolve_local(uri, s.model_root))


def fetch_s3(uri: str) -> dict[str, bytes]:
    try:
        import boto3  # type: ignore[import-not-found]
    except ImportError as e:
        raise Refused("s3 reload is not available in this image") from e
    bucket, _, prefix = uri[len("s3://"):].partition("/")
    c = boto3.client("s3")

    def get(name: str) -> bytes:
        body = c.get_object(Bucket=bucket, Key=f"{prefix.rstrip('/')}/{name}")["Body"]
        return body.read(64 * 1024 * 1024 + 1)

    try:
        files = {"MANIFEST.json": get("MANIFEST.json")}
        for n in json.loads(files["MANIFEST.json"])["files"]:
            if not isinstance(n, str) or "/" in n:
                raise Refused("manifest lists an invalid file name")
            files[n] = get(n)
    except Refused:
        raise
    except Exception as e:  # noqa: BLE001 - unreachable or missing objects
        raise Refused(f"cannot fetch the model: {type(e).__name__}") from e
    return files


# --------------------------------------------------------------------------- handlers
async def _body(request: Request) -> bytes:
    cl = request.headers.get("content-length")
    if cl is not None and (not cl.isdigit() or int(cl) > MAX_BODY_BYTES):
        raise TooLarge("body")
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > MAX_BODY_BYTES:
            raise TooLarge("body")
    return bytes(buf)


def _decide_sync(st: State, raw: bytes) -> tuple[int, dict[str, Any]]:
    m = st.metrics
    b = st.current  # this request's bundle, whatever reload does meanwhile
    t0 = time.perf_counter()
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError):
        m.requests.labels("bad_json").inc()
        return 400, {"error": "body is not valid JSON"}
    try:
        parsed = parse_request(data)
    except TooLarge:
        m.requests.labels("too_large").inc()
        return 413, {"error": "history has more than 1000 items"}
    except RecursionError:
        m.requests.labels("bad_json").inc()
        return 400, {"error": "body is too deeply nested"}
    for k, n in parsed.flags.items():
        if k in FLAG_NAMES:
            m.repaired.labels(k).inc(n)
    version = b.version if b is not None else "none"
    keep = int(round(b.cfg.keep_after_use_s)) if b is not None else None
    reason = None
    if b is None:
        out, reason = empty_answer(version, parsed.truncated, keep), "no_model"
    elif parsed.bad is not None:
        out, reason = empty_answer(version, parsed.truncated, keep), "bad_request"
    else:
        try:
            out = answer(b, parsed)
        except Exception:  # noqa: BLE001 - FR-4: never fail the caller
            log.exception("decide failed")
            out, reason = empty_answer(version, parsed.truncated, keep), "internal"
        else:
            if out["degraded"]:
                reason = "fallback" if out["mode"] not in ("prior",) else None
                reason = reason or "abstain"
    dt = time.perf_counter() - t0
    out["latency_ms"] = round(dt * 1000.0, 3)
    m.latency.observe(dt)
    if reason is not None and reason != "abstain":
        m.degraded.labels(reason).inc()
        st.degraded_count += 1
    m.requests.labels("ok").inc()
    return 200, out


def make_app(settings: Settings | None = None) -> Starlette:
    settings = settings or Settings.from_env()
    metrics = Metrics()
    st = State(settings, metrics)

    def startup() -> None:
        if settings.cold_start_path:
            st.cold = ColdStarts.load(Path(settings.cold_start_path))
        if settings.model_path and st.cold is not None:
            try:
                st.serve(load_bundle(read_folder(Path(settings.model_path)), st.cold))
                metrics.reloads.labels("startup_ok").inc()
            except (BundleError, OSError) as e:
                metrics.reloads.labels("startup_failed").inc()
                log.error("startup model refused: %s", e)

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        startup()
        yield

    async def decide(request: Request) -> Response:
        try:
            raw = await _body(request)
        except TooLarge:
            metrics.requests.labels("too_large").inc()
            return JSONResponse({"error": "body over 256 KB"}, status_code=413)
        code, out = await run_in_threadpool(_decide_sync, st, raw)
        return JSONResponse(out, status_code=code)

    async def healthz(request: Request) -> Response:
        b = st.current
        body = {"status": "ok" if b is not None else "no_model",
                "model_version": b.version if b is not None else None,
                "previous_version": st.previous.version if st.previous is not None else None,
                "degraded_count": st.degraded_count}
        return JSONResponse(body, status_code=200 if b is not None else 503)

    async def prom(request: Request) -> Response:
        return Response(generate_latest(metrics.registry), media_type=CONTENT_TYPE_LATEST)

    def _authorised(request: Request) -> Response | None:
        if settings.admin_token is None:
            metrics.reloads.labels("disabled").inc()
            return JSONResponse({"error": "admin endpoints disabled"}, status_code=403)
        if settings.workers > 1:  # a reload would reach one worker process only
            metrics.reloads.labels("disabled").inc()
            return JSONResponse({"error": "reload needs WORKERS=1; roll the pods instead"},
                                status_code=409)
        got = request.headers.get("x-admin-token", "")
        if not hmac.compare_digest(got.encode("utf-8", "replace"),
                                   settings.admin_token.encode("utf-8")):
            metrics.reloads.labels("unauthorised").inc()
            return JSONResponse({"error": "bad admin token"}, status_code=401)
        return None

    async def reload(request: Request) -> Response:
        if (deny := _authorised(request)) is not None:
            return deny
        try:
            data = json.loads(await _body(request))
            uri = data["model_uri"]
            if not isinstance(uri, str) or not 0 < len(uri) <= 1024:
                raise ValueError
        except (TooLarge, ValueError, KeyError, TypeError, RecursionError):
            metrics.reloads.labels("bad_request").inc()
            return JSONResponse({"error": "body must be {\"model_uri\": str}"}, status_code=400)
        if st.reload_lock.locked():
            metrics.reloads.labels("busy").inc()
            return JSONResponse({"error": "a reload is in progress"}, status_code=409)
        async with st.reload_lock:
            t0 = time.perf_counter()
            try:
                if st.cold is None:
                    raise Refused("no cold-start file loaded")
                cold = st.cold
                b = await run_in_threadpool(lambda: load_bundle(fetch(uri, settings), cold))
                p99 = await run_in_threadpool(_warmup, b)
                if not math.isfinite(p99) or p99 > RELOAD_P99_MS:
                    raise Refused(f"warm-up p99 {p99:.1f} ms is over {RELOAD_P99_MS} ms")
            except (Refused, BundleError, OSError) as e:
                metrics.reloads.labels("refused").inc()
                return JSONResponse({"ok": False, "error": str(e),
                                     "model_version": _v(st.current)}, status_code=422)
            st.serve(b)
            metrics.reloads.labels("ok").inc()
            return JSONResponse({"ok": True, "model_version": b.version,
                                 "previous_version": _v(st.previous),
                                 "seconds": round(time.perf_counter() - t0, 3),
                                 "warmup_p99_ms": round(p99, 3)})

    async def rollback(request: Request) -> Response:
        if (deny := _authorised(request)) is not None:
            return deny
        async with st.reload_lock:
            if st.previous is None:
                metrics.reloads.labels("rollback_refused").inc()
                return JSONResponse({"ok": False, "error": "no previous model"},
                                    status_code=409)
            st.serve(st.previous)
            metrics.reloads.labels("rollback_ok").inc()
            return JSONResponse({"ok": True, "model_version": _v(st.current),
                                 "previous_version": _v(st.previous)})

    app = Starlette(routes=[Route("/decide", decide, methods=["POST"]),
                            Route("/healthz", healthz, methods=["GET"]),
                            Route("/metrics", prom, methods=["GET"]),
                            Route("/reload", reload, methods=["POST"]),
                            Route("/rollback", rollback, methods=["POST"])],
                    lifespan=lifespan)
    app.state.predictor = st
    return app


def _v(b: Bundle | None) -> str | None:
    return b.version if b is not None else None


def _warmup(b: Bundle) -> float:
    return warmup_p99_ms(b, b.golden)
