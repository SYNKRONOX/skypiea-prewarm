"""Cross-site request guard for the Live Console (docs/handoff/REQUESTS_W2.md R9).

The console has no login: it is a local demo bound to 127.0.0.1. Without this guard, any web
page open in the presenter's browser could change its settings with a "simple" cross-site POST
(no CORS preflight), and a DNS-rebinding page could reach it under its own host name.

For every request:
  * Host must be 127.0.0.1:<port>, localhost:<port> or [::1]:<port> (DNS rebinding sends the
    attacker's host name) -> else 403.
For every state-changing request (any method but GET / HEAD):
  * Origin must be present and equal the console's own origin, http://<Host> -> else 403;
  * the custom header X-Skypiea-Client must be present and non-empty -> else 403. The console
    page's JavaScript sends it on every POST; a cross-site page cannot add a custom header
    without a CORS preflight, and the console answers no preflight (no CORS headers at all).
The console never sends Access-Control-Allow-* headers, so other origins cannot read its
responses either.
"""

from __future__ import annotations

import json
import os
from typing import Any

CLIENT_HEADER = "x-skypiea-client"
SAFE_METHODS = frozenset({"GET", "HEAD"})


def console_port() -> int:
    return int(os.environ.get("CONSOLE_PORT", "8090"))


def console_url(port: int | None = None) -> str:
    return f"http://127.0.0.1:{port or console_port()}"


def script_headers(client: str, port: int | None = None) -> dict[str, str]:
    """Headers a local script (demo/console_run.py, console.bakeoff) sends to pass the guard:
    the console's own origin and the client name."""
    return {"Origin": console_url(port), "X-Skypiea-Client": client}


def allowed_hosts(port: int) -> frozenset[str]:
    return frozenset({f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"})


class CrossSiteGuard:
    """Pure ASGI middleware (so the /events stream is not buffered)."""

    def __init__(self, app: Any, port: int):
        self.app, self.port, self.hosts = app, port, allowed_hosts(port)

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":                    # lifespan
            await self.app(scope, receive, send)
            return
        h: dict[str, str] = {}
        for k, v in scope.get("headers", []):
            h.setdefault(k.decode("latin-1").lower(), v.decode("latin-1"))
        host = h.get("host", "").lower()
        why = None
        if host not in self.hosts:
            why = f"host not allowed (open the console at http://127.0.0.1:{self.port})"
        elif scope["method"] not in SAFE_METHODS:
            if h.get("origin") != f"http://{host}":
                why = "cross-origin request refused (Origin must be the console's own)"
            elif not h.get(CLIENT_HEADER, "").strip():
                why = "missing X-Skypiea-Client header"
        if why is None:
            await self.app(scope, receive, send)
            return
        body = json.dumps({"error": why}).encode()
        await send({"type": "http.response.start", "status": 403, "headers": [
            (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
            (b"cache-control", b"no-store")]})
        await send({"type": "http.response.body", "body": body})
