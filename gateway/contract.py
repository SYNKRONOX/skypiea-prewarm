"""Predictor contract (draft v1): request/response types and strict validation.

See docs/predictor_contract.md. The gateway sends ``DecideRequest`` after every tool result and
accepts only a ``DecideResponse`` that passes ``parse_response``; anything else is treated as
"no decision" (FR-4: the gateway then behaves as plain scale-to-zero).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

CONTRACT_VERSION = "v1"
RANK_SUM_TOLERANCE = 1e-6


@dataclass(frozen=True)
class HistoryItem:
    tool_class: str
    t_start_ms: float
    t_end_ms: float
    error: bool
    batch_id: int


@dataclass(frozen=True)
class OracleHint:
    """Only for the oracle baseline: the replayer's true future calls (relative to now_ms)."""

    tool_class: str
    in_ms: float


@dataclass(frozen=True)
class DecideRequest:
    session_id: str
    now_ms: float
    history: list[HistoryItem]
    oracle: list[OracleHint] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        out = {"session_id": self.session_id, "now_ms": self.now_ms,
               "history": [asdict(h) for h in self.history]}
        if self.oracle:
            out["oracle"] = [asdict(o) for o in self.oracle]
        return out

    @staticmethod
    def from_json(d: dict[str, Any]) -> DecideRequest:
        return DecideRequest(
            session_id=str(d["session_id"]), now_ms=float(d["now_ms"]),
            history=[HistoryItem(str(h["tool_class"]), float(h["t_start_ms"]),
                                 float(h["t_end_ms"]), bool(h["error"]), int(h["batch_id"]))
                     for h in d.get("history", [])],
            oracle=[OracleHint(str(o["tool_class"]), float(o["in_ms"]))
                    for o in d.get("oracle", []) or []],
        )


@dataclass(frozen=True)
class Ranked:
    tool_class: str
    p: float


@dataclass(frozen=True)
class Decision:
    tool_class: str
    warm: bool
    copies: int
    start_in_ms: int
    ttl_s: int | None
    prob: float


@dataclass(frozen=True)
class DecideResponse:
    ranked: list[Ranked]
    decisions: list[Decision]
    model_version: str
    latency_ms: float

    def to_json(self) -> dict[str, Any]:
        return {"ranked": [asdict(r) for r in self.ranked],
                "decisions": [asdict(d) for d in self.decisions],
                "model_version": self.model_version, "latency_ms": self.latency_ms}


class ContractError(ValueError):
    """The predictor's answer does not follow the contract."""


def _num(v: Any, name: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ContractError(f"{name} must be a finite number, got {v!r}")
    return float(v)


def _prob(v: Any, name: str) -> float:
    p = _num(v, name)
    if not 0.0 <= p <= 1.0:
        raise ContractError(f"{name} must be in [0, 1], got {p}")
    return p


def parse_response(d: Any, max_copies: int = 10) -> DecideResponse:
    """Validate a /decide answer. Raises ContractError on anything off-contract."""
    if not isinstance(d, dict):
        raise ContractError("response must be a JSON object")
    ranked_raw, dec_raw = d.get("ranked"), d.get("decisions")
    if not isinstance(ranked_raw, list) or not isinstance(dec_raw, list):
        raise ContractError("ranked and decisions must be lists")
    ranked = []
    for i, r in enumerate(ranked_raw):
        if not isinstance(r, dict) or not isinstance(r.get("tool_class"), str):
            raise ContractError(f"ranked[{i}] needs a string tool_class")
        ranked.append(Ranked(r["tool_class"], _prob(r.get("p"), f"ranked[{i}].p")))
    if sum(r.p for r in ranked) > 1.0 + RANK_SUM_TOLERANCE:
        raise ContractError("ranked probabilities sum above 1 (FR-1)")
    decisions = []
    for i, x in enumerate(dec_raw):
        if not isinstance(x, dict) or not isinstance(x.get("tool_class"), str):
            raise ContractError(f"decisions[{i}] needs a string tool_class")
        if not isinstance(x.get("warm"), bool):
            raise ContractError(f"decisions[{i}].warm must be a boolean")
        copies = x.get("copies")
        if isinstance(copies, bool) or not isinstance(copies, int) or not 0 <= copies <= max_copies:
            raise ContractError(f"decisions[{i}].copies must be an int in [0, {max_copies}]")
        start = x.get("start_in_ms", 0)
        if isinstance(start, bool) or not isinstance(start, int) or start < 0:
            raise ContractError(f"decisions[{i}].start_in_ms must be an int >= 0")
        ttl = x.get("ttl_s")
        if ttl is not None and (isinstance(ttl, bool) or not isinstance(ttl, int) or ttl < 0):
            raise ContractError(f"decisions[{i}].ttl_s must be null or an int >= 0")
        decisions.append(Decision(x["tool_class"], x["warm"], copies, start, ttl,
                                  _prob(x.get("prob"), f"decisions[{i}].prob")))
    mv = d.get("model_version")
    if not isinstance(mv, str) or not mv:
        raise ContractError("model_version must be a non-empty string")
    return DecideResponse(ranked, decisions, mv, _num(d.get("latency_ms", 0.0), "latency_ms"))
