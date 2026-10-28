"""Common trace schema shared by every converter, predictor and the simulator.

A *session* is one agent episode. It is a list of *batches*, and a batch holds
every tool call the LLM emitted in one turn. A batch with more than one call is
a parallel tool call. Only metadata is stored (tool names, timings, error
flags), never prompts or arguments.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Call:
    """A single tool invocation."""

    tool: str
    service_ms: float = 0.0
    error: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"tool": self.tool, "service_ms": self.service_ms, "error": self.error}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Call:
        return cls(
            tool=d["tool"],
            service_ms=float(d.get("service_ms", 0.0)),
            error=bool(d.get("error", False)),
        )


@dataclass
class Batch:
    """All calls emitted by the LLM in one turn."""

    batch_id: int
    t_emit_ms: float
    calls: list[Call] = field(default_factory=list)
    node: str | None = None

    @property
    def tools(self) -> frozenset[str]:
        """The distinct tools in this batch."""
        return frozenset(c.tool for c in self.calls)

    @property
    def is_parallel(self) -> bool:
        return len(self.calls) > 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "t_emit_ms": self.t_emit_ms,
            "calls": [c.to_dict() for c in self.calls],
            "node": self.node,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Batch:
        return cls(
            batch_id=int(d["batch_id"]),
            t_emit_ms=float(d["t_emit_ms"]),
            calls=[Call.from_dict(c) for c in d.get("calls", [])],
            node=d.get("node"),
        )


@dataclass
class Session:
    """One agent episode.

    ``source`` names the dataset (e.g. ``"tau-bench"``). ``group`` is the task
    or user id, used to split train and test without leakage.
    """

    session_id: str
    source: str
    group: str
    batches: list[Batch] = field(default_factory=list)

    def tool_sequence(self) -> list[str]:
        """Flatten batches into one tool sequence.

        Calls inside a batch have no inherent order, so they are sorted by name
        to keep the sequence deterministic.
        """
        seq: list[str] = []
        for b in self.batches:
            seq.extend(sorted(c.tool for c in b.calls))
        return seq

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "source": self.source,
            "group": self.group,
            "batches": [b.to_dict() for b in self.batches],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Session:
        return cls(
            session_id=d["session_id"],
            source=d["source"],
            group=d["group"],
            batches=[Batch.from_dict(b) for b in d.get("batches", [])],
        )
