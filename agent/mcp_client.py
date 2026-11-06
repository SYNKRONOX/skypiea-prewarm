"""Minimal MCP client for the Skypiea gateway's public endpoint (POST /mcp, Streamable HTTP,
JSON responses, protocol 2026-07-28). Talks HTTP only: nothing from gateway/ or controller/
is imported.

The gateway assigns a session on the first request and returns it in ``Mcp-Session-Id``; the
client sends it back on every later request and ends the session with DELETE on close.
Per call the gateway reports, in response headers, whether the call was cold, the latency it
added, how long it held the call, and whether the tool had been predicted (warmed).
"""

from __future__ import annotations

import itertools
import time
from dataclasses import dataclass
from typing import Any

import httpx

PROTOCOL = "2026-07-28"
SESSION_HEADER = "Mcp-Session-Id"
META = {"io.modelcontextprotocol/protocolVersion": PROTOCOL,
        "io.modelcontextprotocol/clientCapabilities": {}}


class McpError(RuntimeError):
    pass


@dataclass(frozen=True)
class CallInfo:
    ok: bool
    cold: bool | None
    added_ms: float | None
    held_ms: float | None
    predicted: bool | None
    wall_ms: float
    error: str | None = None
    warm_source: str | None = None   # "hint" | "call" (gateway with the turn hint on only)
    hold_late_ms: float | None = None  # event-loop lateness after the hold (gateway >= W1)


def _f(h: httpx.Headers, k: str) -> float | None:
    try:
        return float(h[k])
    except (KeyError, ValueError):
        return None


def _b(h: httpx.Headers, k: str) -> bool | None:
    return None if k not in h else h[k] == "1"


class McpClient:
    def __init__(self, url: str, timeout_s: float = 180.0, client: httpx.Client | None = None):
        self.url = url
        self._http = client or httpx.Client(timeout=timeout_s)
        self.timeout_s = timeout_s
        self.session_id: str | None = None
        self._ids = itertools.count(1)

    def _headers(self, method: str, name: str | None = None) -> dict[str, str]:
        h = {"accept": "application/json, text/event-stream", "content-type": "application/json",
             "MCP-Protocol-Version": PROTOCOL, "Mcp-Method": method}
        if name:
            h["Mcp-Name"] = name
        if self.session_id:
            h[SESSION_HEADER] = self.session_id
        return h

    def _post(self, method: str, params: dict[str, Any], name: str | None = None
              ) -> tuple[dict[str, Any], httpx.Response]:
        body = {"jsonrpc": "2.0", "id": next(self._ids), "method": method,
                "params": {**params, "_meta": META}}
        r = self._http.post(self.url, json=body, headers=self._headers(method, name),
                            timeout=self.timeout_s)
        if sid := r.headers.get(SESSION_HEADER):
            self.session_id = sid
        try:
            data = r.json()
        except ValueError:
            raise McpError(f"{method}: HTTP {r.status_code}, not JSON") from None
        if not isinstance(data, dict):
            raise McpError(f"{method}: unexpected answer type {type(data).__name__}")
        return data, r

    def list_tools(self) -> list[str]:
        data, _ = self._post("tools/list", {})
        if "result" not in data:
            raise McpError(f"tools/list failed: {data.get('error')}")
        return [t["name"] for t in data["result"].get("tools", [])]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> tuple[Any, CallInfo]:
        """(result payload or None, call info). Transport errors become ok=False, not raises."""
        t = time.monotonic()
        try:
            data, r = self._post("tools/call", {"name": name, "arguments": arguments}, name)
        except (httpx.HTTPError, McpError) as e:
            return None, CallInfo(False, None, None, None, None,
                                  (time.monotonic() - t) * 1000, f"{type(e).__name__}: {e}"[:200])
        wall = (time.monotonic() - t) * 1000
        h = r.headers
        res = data.get("result")
        ok = isinstance(res, dict) and not res.get("isError")
        err = None
        if not ok:
            err = str(data.get("error") or "tool returned isError")[:200]
        info = CallInfo(ok, _b(h, "X-Skypiea-Cold"), _f(h, "X-Skypiea-Added-Ms"),
                        _f(h, "X-Skypiea-Held-Ms"), _b(h, "X-Skypiea-Predicted"), wall, err,
                        h.get("X-Skypiea-Warm-Source"), _f(h, "X-Skypiea-Hold-Late-Ms"))
        return res, info

    def close(self) -> None:
        if self.session_id:
            try:
                self._http.delete(self.url, headers={SESSION_HEADER: self.session_id},
                                  timeout=10)
            except httpx.HTTPError:
                pass
        self.session_id = None
