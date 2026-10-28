"""Common trace schema shared by every converter, predictor, the replayer and the simulator.

A *session* is one agent episode. It is a list of *batches*, and a batch holds every tool
call the LLM emitted in one turn. A batch with more than one call is a parallel tool call.
Only metadata is stored (tool names, timings, error flags), never prompts or arguments.

The schema is a superset of the PRD trace format (PRD v1.1, Sec. 7)::

    PRD                      ours
    session_id           ->  Session.session_id
    trace_type           ->  Session.trace_type ("real" for converted datasets)
    events[]             ->  Session.batches[] (one PRD event = one batch)
    events[].t_offset_ms ->  Batch.t_offset_ms
    events[].node        ->  Batch.node
    events[].tool_called ->  Batch.calls[].tool (None -> a batch with no calls)

``Session.to_prd()`` and ``Session.from_prd()`` convert to and from the PRD format.
Timing is never invented: sources without it have ``has_timing=False`` and ``None`` times.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _opt_float(x: Any) -> float | None:
    return None if x is None else float(x)


@dataclass
class Call:
    """A single tool invocation.

    ``tool`` is the mapped tool class, ``raw_tool`` the name in the source data.
    ``service_ms`` is the recorded tool run time, or ``None`` if the source has none.
    """

    tool: str
    service_ms: float | None = None
    error: bool = False
    raw_tool: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "service_ms": self.service_ms,
            "error": self.error,
            "raw_tool": self.raw_tool,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Call:
        return cls(
            tool=d["tool"],
            service_ms=_opt_float(d.get("service_ms")),
            error=bool(d.get("error", False)),
            raw_tool=d.get("raw_tool"),
        )


@dataclass
class Batch:
    """All calls emitted by the LLM in one turn.

    ``t_offset_ms`` is milliseconds since session start (PRD name), or ``None`` if the
    source has no timing. A batch with no calls is a node-only PRD event.
    """

    batch_id: int
    t_offset_ms: float | None
    node: str | None = None
    calls: list[Call] = field(default_factory=list)

    @property
    def tools(self) -> frozenset[str]:
        """The distinct tool classes in this batch."""
        return frozenset(c.tool for c in self.calls)

    @property
    def is_parallel(self) -> bool:
        return len(self.calls) > 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "t_offset_ms": self.t_offset_ms,
            "node": self.node,
            "calls": [c.to_dict() for c in self.calls],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Batch:
        return cls(
            batch_id=int(d["batch_id"]),
            t_offset_ms=_opt_float(d.get("t_offset_ms")),
            node=d.get("node"),
            calls=[Call.from_dict(c) for c in d.get("calls", [])],
        )


@dataclass
class Session:
    """One agent episode.

    ``source`` names the dataset (e.g. ``"tau_bench"``). ``group`` is the task or user id,
    used to split train and test without leakage.
    """

    session_id: str
    source: str
    group: str
    has_timing: bool = False
    batches: list[Batch] = field(default_factory=list)
    trace_type: str = "real"

    def tool_sequence(self) -> list[str]:
        """Flatten batches into one tool-class sequence.

        Calls inside a batch have no inherent order, so they are sorted by name to keep
        the sequence deterministic. Batches with no calls contribute nothing.
        """
        seq: list[str] = []
        for b in self.batches:
            seq.extend(sorted(c.tool for c in b.calls))
        return seq

    def batch_sequence(self) -> list[frozenset[str]]:
        """The tool-class set of each non-empty batch, in order."""
        return [b.tools for b in self.batches if b.calls]

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "source": self.source,
            "group": self.group,
            "has_timing": self.has_timing,
            "trace_type": self.trace_type,
            "batches": [b.to_dict() for b in self.batches],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Session:
        return cls(
            session_id=d["session_id"],
            source=d["source"],
            group=d["group"],
            has_timing=bool(d.get("has_timing", False)),
            batches=[Batch.from_dict(b) for b in d.get("batches", [])],
            trace_type=d.get("trace_type", "real"),
        )

    def to_prd(self) -> dict[str, Any]:
        """Export in the PRD trace format: one event per call (calls in a batch share
        its time and node), and one ``tool_called: null`` event per empty batch."""
        events: list[dict[str, Any]] = []
        for b in self.batches:
            tools: list[str | None] = sorted(c.tool for c in b.calls) or [None]
            for t in tools:
                events.append({"t_offset_ms": b.t_offset_ms, "node": b.node, "tool_called": t})
        return {"session_id": self.session_id, "trace_type": self.trace_type, "events": events}

    @classmethod
    def from_prd(cls, d: dict[str, Any], source: str = "prd", group: str | None = None) -> Session:
        """Import a PRD trace. Each event becomes one batch (PRD traces are sequential)."""
        batches = [
            Batch(
                batch_id=i,
                t_offset_ms=_opt_float(e.get("t_offset_ms")),
                node=e.get("node"),
                calls=[Call(e["tool_called"], raw_tool=e["tool_called"])]
                if e.get("tool_called")
                else [],
            )
            for i, e in enumerate(d["events"])
        ]
        return cls(
            session_id=d["session_id"],
            source=source,
            group=group or d["session_id"],
            has_timing=all(b.t_offset_ms is not None for b in batches),
            batches=batches,
            trace_type=d.get("trace_type", "real"),
        )
