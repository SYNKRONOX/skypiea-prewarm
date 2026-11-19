"""ControllerClient: how the gateway asks the controller for warm pods and reports in-flight
calls (controller HTTP API: POST /v1/warm, POST /v1/inflight)."""

from __future__ import annotations

from typing import Protocol

import httpx


class ControllerClient(Protocol):
    async def warm(self, tool_class: str, copies: int, ttl_s: int | None, reason: str) -> None: ...

    async def inflight(self, gateway_id: str, counts: dict[str, int]) -> None: ...


class HttpControllerClient:
    def __init__(self, url: str, timeout_s: float = 0.5, client: httpx.AsyncClient | None = None,
                 admin_token: str | None = None):
        self.url, self.timeout_s = url.rstrip("/"), timeout_s
        self.client = client or httpx.AsyncClient()
        self.headers = {"X-Admin-Token": admin_token} if admin_token else {}   # issue #23

    async def warm(self, tool_class: str, copies: int, ttl_s: int | None, reason: str) -> None:
        r = await self.client.post(f"{self.url}/v1/warm", timeout=self.timeout_s,
                                   headers=self.headers, json={
            "tool_class": tool_class, "copies": copies, "ttl_s": ttl_s, "reason": reason})
        r.raise_for_status()

    async def scale_failing(self, server: str) -> bool:
        """Issue #4: does the controller report that scaling this server up is failing?"""
        r = await self.client.get(f"{self.url}/v1/state", timeout=self.timeout_s)
        r.raise_for_status()
        return bool((r.json().get(server) or {}).get("scale_failing"))

    async def inflight(self, gateway_id: str, counts: dict[str, int]) -> None:
        r = await self.client.post(f"{self.url}/v1/inflight", timeout=self.timeout_s,
                                   json={"gateway_id": gateway_id, "counts": counts})
        r.raise_for_status()


class RecordingController:
    """Test double: records calls; ``on_warm`` can simulate the cluster reacting."""

    def __init__(self, on_warm=None, fail: Exception | None = None):
        self.warms: list[tuple[str, int, int | None, str]] = []
        self.inflights: list[dict[str, int]] = []
        self.on_warm, self.fail = on_warm, fail

    async def warm(self, tool_class: str, copies: int, ttl_s: int | None, reason: str) -> None:
        self.warms.append((tool_class, copies, ttl_s, reason))
        if self.fail:
            raise self.fail
        if self.on_warm:
            await self.on_warm(tool_class, copies)

    async def inflight(self, gateway_id: str, counts: dict[str, int]) -> None:
        self.inflights.append(dict(counts))
