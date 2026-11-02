"""Shared bootstrap for every Skypiea MCP tool server.

Provides: an MCPServer on Streamable HTTP (stateless, JSON responses, protocol 2026-07-28),
GET /healthz, GET /metrics (Prometheus), JSON logs on stdout, and ``burn()``, which spends
``service_ms`` of real CPU or file I/O work so replayed calls take their recorded duration.

Startup cost is whatever the tool module imports and loads before calling ``run()``: the HTTP
port only opens once that work is done, so /healthz answering == the tool is ready.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import tempfile
import time
from collections.abc import Callable
from typing import Any

import anyio
import uvicorn
from mcp.server.mcpserver import MCPServer
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

DEFAULT_SERVICE_MS = int(os.environ.get("SKYPIEA_SERVICE_MS", "20"))
MAX_SERVICE_MS = int(os.environ.get("SKYPIEA_MAX_SERVICE_MS", "120000"))
PORT = int(os.environ.get("PORT", "8080"))

CALLS = Counter("skypiea_tool_calls_total", "Tool calls", ["tool", "status"])
CALL_SECONDS = Histogram(
    "skypiea_tool_call_seconds",
    "Tool call wall time",
    ["tool"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120),
)
STARTUP_SECONDS = Gauge(
    "skypiea_tool_startup_seconds", "Process start to HTTP server ready", ["tool"]
)


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "ts": round(record.created, 6),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        extra = getattr(record, "fields", None)
        if extra:
            out.update(extra)
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


def _setup_logging() -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(os.environ.get("LOG_LEVEL", "INFO"))
    return logging.getLogger("skypiea.tool")


log = _setup_logging()


def process_age_s() -> float:
    """Seconds since this process was started by the kernel (Linux), else since import."""
    try:
        with open("/proc/self/stat") as f:
            fields = f.read().rsplit(")", 1)[1].split()
        start_ticks = int(fields[19])  # field 22 overall: starttime in clock ticks after boot
        with open("/proc/uptime") as f:
            uptime = float(f.read().split()[0])
        return uptime - start_ticks / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, IndexError):
        return time.perf_counter() - _IMPORT_T0


_IMPORT_T0 = time.perf_counter()
_STATE: dict[str, float] = {}


def _burn_cpu(deadline: float) -> int:
    h = hashlib.sha256(b"skypiea")
    n = 0
    while time.perf_counter() < deadline:
        for _ in range(200):
            h.update(h.digest())
        n += 200
    return n


def _burn_io(deadline: float) -> int:
    chunk = os.urandom(64 * 1024)
    n = 0
    with tempfile.TemporaryFile(dir=os.environ.get("SKYPIEA_SCRATCH", None)) as f:
        while time.perf_counter() < deadline:
            f.write(chunk)
            f.flush()
            f.seek(0)
            f.read(64 * 1024)
            f.seek(0, os.SEEK_END)
            n += 1
            if f.tell() > 32 * 1024 * 1024:  # cap the scratch file at 32 MiB
                f.seek(0)
                f.truncate()
    return n


async def burn(service_ms: int | None, work: str = "cpu") -> int:
    """Spend ``service_ms`` milliseconds of real work in a worker thread. Returns loop count."""
    ms = DEFAULT_SERVICE_MS if service_ms is None else int(service_ms)
    ms = max(0, min(ms, MAX_SERVICE_MS))
    if ms == 0:
        return 0
    deadline = time.perf_counter() + ms / 1000.0
    fn: Callable[[float], int] = _burn_io if work == "io" else _burn_cpu
    return await anyio.to_thread.run_sync(fn, deadline)


def create_server(name: str, description: str = "") -> MCPServer:
    mcp = MCPServer(name, version=os.environ.get("SKYPIEA_VERSION", "0.1.0"),
                    description=description or None)

    @mcp.custom_route("/healthz", methods=["GET"], include_in_schema=False)
    async def healthz(_: Request) -> Response:
        # uptime_s lets E0 place the process start on its own clock (k8s timestamps are 1 s).
        return JSONResponse({"status": "ok", "tool": name, "uptime_s": round(process_age_s(), 4),
                             "startup_s": _STATE.get("startup_s")})

    @mcp.custom_route("/metrics", methods=["GET"], include_in_schema=False)
    async def metrics(_: Request) -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return mcp


def instrument(tool: str):
    """Decorator for tool coroutines: Prometheus metrics and one JSON log line per call."""

    def wrap(fn):
        import functools

        @functools.wraps(fn)
        async def inner(*args, **kwargs):
            t0 = time.perf_counter()
            status = "ok"
            try:
                return await fn(*args, **kwargs)
            except Exception:
                status = "error"
                raise
            finally:
                dt = time.perf_counter() - t0
                CALLS.labels(tool, status).inc()
                CALL_SECONDS.labels(tool).observe(dt)
                log.info("tool_call", extra={"fields": {
                    "tool": tool, "status": status, "duration_ms": round(dt * 1000, 2),
                    "service_ms": kwargs.get("service_ms"),
                }})

        return inner

    return wrap


def run(mcp: MCPServer, tool: str, init_info: dict[str, Any] | None = None) -> None:
    """Start serving. Call this after all startup work (imports, data/model loading)."""
    app = mcp.streamable_http_app(
        json_response=True, stateless_http=True, host="0.0.0.0", streamable_http_path="/mcp"
    )

    server = uvicorn.Server(uvicorn.Config(
        app, host="0.0.0.0", port=PORT, log_level="warning", access_log=False, lifespan="on",
    ))

    async def main() -> None:
        async with anyio.create_task_group() as tg:
            tg.start_soon(server.serve)
            while not server.started:
                if server.should_exit:
                    return
                await anyio.sleep(0.005)
            age = process_age_s()
            STARTUP_SECONDS.labels(tool).set(age)
            _STATE["startup_s"] = round(age, 4)
            log.info("ready", extra={"fields": {
                "tool": tool, "port": PORT, "startup_ms": round(age * 1000, 1),
                **(init_info or {}),
            }})

    anyio.run(main)
