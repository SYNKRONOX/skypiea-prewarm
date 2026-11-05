"""Predictor PLACEHOLDER service (POST /decide, contract v1). Stand-in policies for functional
checks only, until the model branch's predictors/policies.py and its service are merged.

These are NOT the project's predictors: no model is trained here and no number they produce
is a result. They exist so the gateway/controller path can be exercised end to end under the
same decide() interface the real service will expose.

Policies (select with SKYPIEA_POLICY or POST /admin/policy {"name": ..., "params": {...}}):
  scale_to_zero   never warms anything
  always_warm     asks for every server, with a long TTL, on every decide
  keep_alive      keeps the tool that just ran warm for T seconds (params: ttl_s, default 30)
  markov1         SpecBox-style first-order Markov, top-1 only, warm if p >= tau (0.6); learns
                  transitions online from the histories it is sent
  predictive      online n-gram (order 2, backs off to 1) warming every next tool with
                  p >= tau (0.3); stand-in for the ranker
  oracle          warms exactly the replayer's true next calls (DecideRequest.oracle), early
                  enough to cover the measured cached cold start (configs/cold_start.yaml)
"""

from __future__ import annotations

import math
import os
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import yaml
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from gateway.contract import DecideRequest
from tools.common.toolmap import load

ROOT = Path(__file__).resolve().parent.parent
COLD_START = Path(os.environ.get("SKYPIEA_COLD_START", ROOT / "configs" / "cold_start.yaml"))


def _servers_and_lead() -> tuple[dict[str, str], dict[str, float]]:
    """First class of each server (to name a server in a decision) and cached cold-start median
    per server (oracle lead time)."""
    tm = load()
    first = {s: (spec.classes[0] if spec.classes else s) for s, spec in tm.servers.items()}
    lead: dict[str, float] = {}
    if COLD_START.exists():
        doc = yaml.safe_load(COLD_START.read_text())
        lead = {s: float(v["cached"]["median_ms"]) for s, v in doc.get("servers", {}).items()}
    return first, lead


def _answer(name: str, ranked: dict[str, float], decisions: list[dict], t0: float) -> dict:
    tot = sum(ranked.values())
    if tot > 1.0:
        ranked = {k: v / tot for k, v in ranked.items()}
    # floor to 6 decimals (2026-11-05): rounding could push the sum just above 1, which the
    # gateway's contract check rejects; flooring keeps sum(ranked) <= 1
    return {"ranked": [{"tool_class": k, "p": math.floor(v * 1e6) / 1e6}
                       for k, v in sorted(ranked.items(), key=lambda kv: -kv[1])],
            "decisions": decisions, "model_version": f"stub-{name}",
            "latency_ms": round((time.perf_counter() - t0) * 1000, 3)}


def _dec(cls: str, prob: float, ttl_s: int | None, copies: int = 1, start_in_ms: int = 0) -> dict:
    return {"tool_class": cls, "warm": True, "copies": copies, "start_in_ms": int(start_in_ms),
            "ttl_s": ttl_s, "prob": round(min(max(prob, 0.0), 1.0), 6)}


class StubPredictor:
    def __init__(self, name: str = "scale_to_zero", params: dict[str, Any] | None = None):
        self.first, self.lead = _servers_and_lead()
        self.tm = load()
        self.set_policy(name, params or {})

    def set_policy(self, name: str, params: dict[str, Any]) -> None:
        if name not in POLICIES:
            raise ValueError(f"unknown policy {name}; one of {sorted(POLICIES)}")
        self.name, self.params = name, params
        self.uni: Counter[str] = Counter()
        self.bi: dict[str, Counter[str]] = defaultdict(Counter)
        self.tri: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
        self.seen: dict[str, int] = {}  # session -> history length already learned

    # online learning of transitions (markov1, predictive): each call is learned once
    def _learn(self, req: DecideRequest) -> list[str]:
        seq = [h.tool_class for h in req.history]
        start = self.seen.get(req.session_id, 0)
        for i in range(start, len(seq)):
            self.uni[seq[i]] += 1
            if i >= 1:
                self.bi[seq[i - 1]][seq[i]] += 1
            if i >= 2:
                self.tri[(seq[i - 2], seq[i - 1])][seq[i]] += 1
        self.seen[req.session_id] = len(seq)
        return seq

    def decide(self, req: DecideRequest) -> dict:
        return POLICIES[self.name](self, req, time.perf_counter())


def _scale_to_zero(self: StubPredictor, req: DecideRequest, t0: float) -> dict:
    return _answer("scale_to_zero", {}, [], t0)


def _always_warm(self: StubPredictor, req: DecideRequest, t0: float) -> dict:
    ttl = int(self.params.get("ttl_s", 3600))
    return _answer("always_warm", {}, [_dec(c, 1.0, ttl) for c in self.first.values()], t0)


def _keep_alive(self: StubPredictor, req: DecideRequest, t0: float) -> dict:
    if not req.history:
        return _answer("keep_alive", {}, [], t0)
    return _answer("keep_alive", {}, [_dec(req.history[-1].tool_class, 1.0,
                                          int(self.params.get("ttl_s", 30)))], t0)


def _dist(c: Counter[str]) -> dict[str, float]:
    n = sum(c.values())
    return {k: v / n for k, v in c.items()} if n else {}


def _markov1(self: StubPredictor, req: DecideRequest, t0: float) -> dict:
    seq = self._learn(req)
    probs = _dist(self.bi.get(seq[-1], Counter())) if seq else {}
    tau = float(self.params.get("tau", 0.6))
    decs = []
    if probs:
        top, p = max(probs.items(), key=lambda kv: (kv[1], kv[0]))
        if p >= tau:
            decs = [_dec(top, p, self.params.get("ttl_s"))]
    return _answer("markov1", probs, decs, t0)


def _predictive(self: StubPredictor, req: DecideRequest, t0: float) -> dict:
    seq = self._learn(req)
    probs: dict[str, float] = {}
    if len(seq) >= 2 and self.tri.get((seq[-2], seq[-1])):
        probs = _dist(self.tri[(seq[-2], seq[-1])])
    elif seq and self.bi.get(seq[-1]):
        probs = _dist(self.bi[seq[-1]])
    tau = float(self.params.get("tau", 0.3))
    decs = [_dec(c, p, self.params.get("ttl_s")) for c, p in probs.items() if p >= tau]
    return _answer("predictive", probs, decs, t0)


def _oracle(self: StubPredictor, req: DecideRequest, t0: float) -> dict:
    decs = []
    for o in req.oracle:
        server = self.tm.server_for(o.tool_class) or self.tm.fallback
        lead = self.lead.get(server, 0.0)
        decs.append(_dec(o.tool_class, 1.0, self.params.get("ttl_s"),
                         start_in_ms=max(0, int(o.in_ms - lead))))
    return _answer("oracle", {}, decs, t0)


POLICIES = {"scale_to_zero": _scale_to_zero, "always_warm": _always_warm,
            "keep_alive": _keep_alive, "markov1": _markov1, "predictive": _predictive,
            "oracle": _oracle}


def create_app(stub: StubPredictor | None = None) -> Starlette:
    stub = stub or StubPredictor(os.environ.get("SKYPIEA_POLICY", "scale_to_zero"))

    async def decide(request: Request) -> JSONResponse:
        try:
            req = DecideRequest.from_json(await request.json())
        except (ValueError, KeyError, TypeError) as e:
            return JSONResponse({"error": f"bad request: {e}"}, status_code=400)
        return JSONResponse(stub.decide(req))

    async def policy(request: Request) -> JSONResponse:
        if request.method == "POST":
            d = await request.json()
            try:
                stub.set_policy(d["name"], d.get("params") or {})
            except (KeyError, ValueError) as e:
                return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse({"policy": stub.name, "params": stub.params})

    async def healthz(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "policy": stub.name, "stub": True})

    return Starlette(routes=[Route("/decide", decide, methods=["POST"]),
                             Route("/admin/policy", policy, methods=["GET", "POST"]),
                             Route("/healthz", healthz, methods=["GET"])])
