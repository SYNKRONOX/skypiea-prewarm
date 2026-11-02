"""Model-side view of sessions: events, decision points and prediction contexts.

A session's calls become a flat tuple of ``Event`` (calls inside a batch sorted by tool name,
sharing the batch's time and id); this is exactly what the predictor service receives.

A *decision point* sits before each non-empty batch ``b``. The model sees the events of all
earlier batches (capped at the last ``MAX_HISTORY`` events, as in the service) and the
workflow node of the most recent earlier batch (``None`` for real traces, which have no
nodes). Predicting inside a parallel batch is not useful for prewarming (the other calls are
already in flight), so there are no decision points there.

``Context`` holds the full event tuple plus ``upto``; models must only read
``events[:upto]`` through ``prefix()`` / ``last()``. The leakage property test replaces
``events[upto:]`` with junk to check that they do.
"""

from __future__ import annotations

import math
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field

from traces.schema import Session

MAX_HISTORY = 200
END = "<end>"


@dataclass(frozen=True, slots=True)
class Event:
    """One call, as the service receives it."""

    tool: str
    t_offset_ms: float | None = None
    batch_id: int = 0
    service_ms: float | None = None
    error: bool = False


@dataclass(frozen=True, slots=True)
class Context:
    """What a model may see at a decision point: ``events[:upto]`` and ``node``."""

    events: Sequence[Event]
    upto: int
    node: str | None = None

    def prefix(self) -> Sequence[Event]:
        lo = max(0, self.upto - MAX_HISTORY)
        return self.events[lo:self.upto]

    def last(self, n: int) -> Sequence[Event]:
        if n <= 0:
            return ()
        lo = max(0, self.upto - min(n, MAX_HISTORY))
        return self.events[lo:self.upto]

    def tools(self, n: int) -> tuple[str, ...]:
        """The last ``n`` tools (fewer near the session start)."""
        return tuple(e.tool for e in self.last(n))

    @property
    def length(self) -> int:
        return min(self.upto, MAX_HISTORY)

    @classmethod
    def of(cls, events: Sequence[Event], node: str | None = None) -> Context:
        return cls(tuple(events), len(events), node)


@dataclass
class Episode:
    """A session prepared for modelling.

    ``batches`` lists, per non-empty batch, its (start, end) slice into ``events``;
    ``nodes[i]`` is the workflow node visible before non-empty batch ``i``.
    """

    session_id: str
    source: str
    group: str
    has_timing: bool
    events: tuple[Event, ...]
    batches: list[tuple[int, int]]
    nodes: list[str | None]
    errors_before: list[bool] = field(default_factory=list)

    @property
    def n_batches(self) -> int:
        return len(self.batches)

    def batch_tools(self, i: int) -> list[str]:
        lo, hi = self.batches[i]
        return [e.tool for e in self.events[lo:hi]]

    def context(self, i: int) -> Context:
        """Context before non-empty batch ``i`` (``i == n_batches`` is the end point)."""
        upto = self.batches[i][0] if i < self.n_batches else len(self.events)
        node = self.nodes[i] if i < len(self.nodes) else self.nodes[-1] if self.nodes else None
        return Context(self.events, upto, node)

    def end_node(self) -> str | None:
        return self.nodes[-1] if self.nodes else None


def _finite(x: float | None) -> float | None:
    return x if x is not None and math.isfinite(x) else None


def to_episode(s: Session) -> Episode:
    events: list[Event] = []
    spans: list[tuple[int, int]] = []
    nodes: list[str | None] = []
    errors_before: list[bool] = []
    node: str | None = None
    prev_error = False
    for b in s.batches:
        if b.calls:
            nodes.append(node)
            errors_before.append(prev_error)
            lo = len(events)
            for c in sorted(b.calls, key=lambda c: c.tool):
                events.append(Event(c.tool, _finite(b.t_offset_ms), b.batch_id,
                                    _finite(c.service_ms), bool(c.error)))
            spans.append((lo, len(events)))
            prev_error = any(c.error for c in b.calls)
        if b.node is not None:
            node = b.node
    # The end decision point sees the last node too.
    nodes.append(node)
    return Episode(s.session_id, s.source, s.group, s.has_timing, tuple(events), spans,
                   nodes, errors_before)


def episodes(sessions: Sequence[Session]) -> list[Episode]:
    return [e for e in (to_episode(s) for s in sessions) if e.batches]


def decision_points(ep: Episode) -> Iterator[tuple[int, Context]]:
    """(batch index, context) before each non-empty batch."""
    for i in range(ep.n_batches):
        yield i, ep.context(i)


def targets_ahead(ep: Episode, i: int, k: int) -> list[str]:
    """Tools of the k-th next batch from decision point ``i`` (empty if past the end)."""
    j = i + k - 1
    return ep.batch_tools(j) if j < ep.n_batches else []
