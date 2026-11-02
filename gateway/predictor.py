"""PredictorClient: the gateway's view of the predictor (contract: docs/predictor_contract.md).

Implementations: HttpPredictorClient (production), InProcessPredictorClient (simulator/tests:
wraps any ``decide(DecideRequest) -> dict``), FakePredictor (tests: scripted answers).
The gateway never trusts an answer it has not validated with ``contract.parse_response``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, Protocol

import httpx

from gateway.contract import DecideRequest, DecideResponse, parse_response


class PredictorClient(Protocol):
    async def decide(self, req: DecideRequest) -> DecideResponse: ...


class HttpPredictorClient:
    def __init__(self, url: str, timeout_s: float = 0.3, client: httpx.AsyncClient | None = None):
        self.url, self.timeout_s = url.rstrip("/") + "/decide", timeout_s
        self.client = client or httpx.AsyncClient()

    async def decide(self, req: DecideRequest) -> DecideResponse:
        r = await self.client.post(self.url, json=req.to_json(), timeout=self.timeout_s)
        r.raise_for_status()
        return parse_response(r.json())


class InProcessPredictorClient:
    """Wraps a policy callable ``decide(DecideRequest) -> dict`` (the contract JSON)."""

    def __init__(self, decide: Callable[[DecideRequest], dict[str, Any]]):
        self._decide = decide

    async def decide(self, req: DecideRequest) -> DecideResponse:
        return parse_response(self._decide(req))


class FakePredictor:
    """Scripted predictor for tests: returns ``answer`` (a dict, or a callable of the request),
    optionally after ``delay_s``; raises ``fail`` if set. Records every request."""

    def __init__(self, answer: Any = None, delay_s: float = 0.0, fail: Exception | None = None):
        self.answer, self.delay_s, self.fail = answer, delay_s, fail
        self.requests: list[DecideRequest] = []

    async def decide(self, req: DecideRequest) -> DecideResponse:
        self.requests.append(req)
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if self.fail is not None:
            raise self.fail
        ans = self.answer(req) if callable(self.answer) else self.answer
        if ans is None:
            ans = {"ranked": [], "decisions": [], "model_version": "fake", "latency_ms": 0.0}
        return parse_response(ans)
