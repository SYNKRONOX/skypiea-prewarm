"""Allowed-edge sets for the PRD graph prior.

``graph_prior(j) = 1`` for an allowed next tool ``j`` and 0 otherwise, renormalised (PRD).
Two sources of allowed edges:

- ``WorkflowEdges``: the PRD scenario graphs in configs/model/workflow_graphs.yaml, used when
  the workflow node is known (the synthetic traces). Node-only steps (``tool: null``) are
  passed through, so the allowed next *tools* of ``lookup_order`` are policy_check,
  refund_issue and ``<end>`` (via notify_user).
- ``LearnedEdges``: tool -> tool edges observed in TRAIN with support >= ``min_support``,
  for real traces, which have no workflow nodes. These are "learned edges, not
  workflow-defined".

Both answer ``allowed(ctx, k)``: the tools that can be the k-th next call, or ``None`` when
there is no information (unknown node or unseen last tool), in which case the prior is off.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from predictors.data import END, Context, Episode

ROOT = Path(__file__).resolve().parent.parent
GRAPHS = ROOT / "configs" / "model" / "workflow_graphs.yaml"
START = "<start>"


@lru_cache(maxsize=4)
def _scenarios(path: Path = GRAPHS) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["scenarios"]


def scenario_for(source: str) -> str | None:
    """The workflow scenario behind a source, if any (synthetic_<kind> -> kind)."""
    if source.startswith("synthetic_"):
        kind = source.removeprefix("synthetic_")
        return kind if kind in _scenarios() else None
    return None


class WorkflowEdges:
    """Allowed next tools from the PRD workflow graph of one scenario."""

    label = "workflow-defined edges"

    def __init__(self, scenario: str, path: Path = GRAPHS) -> None:
        self.scenario = scenario
        self.nodes: dict[str, Any] = _scenarios(path)[scenario]["nodes"]
        self._cache = {(n, k): self._reach(n, k) for n in self.nodes for k in range(1, 6)}

    def _next_tools(self, node: str) -> set[tuple[str, str]]:
        """(tool, node) pairs of the next tool-calling steps after ``node``."""
        out: set[tuple[str, str]] = set()
        stack, seen = [node], set()
        while stack:
            n = stack.pop()
            for nxt, p in self.nodes[n]["next"].items():
                if p <= 0:
                    continue
                if nxt == END:
                    out.add((END, END))
                elif self.nodes[nxt]["tool"] is not None:
                    out.add((self.nodes[nxt]["tool"], nxt))
                elif nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        return out

    def _reach(self, node: str, k: int) -> frozenset[str]:
        frontier = {node}
        tools: set[str] = set()
        for _ in range(k):
            tools, nxt_frontier = set(), set()
            for n in frontier:
                for tool, nn in self._next_tools(n):
                    tools.add(tool)
                    if nn != END:
                        nxt_frontier.add(nn)
            frontier = nxt_frontier
            if END in tools:
                tools.add(END)
        return frozenset(tools)

    def allowed(self, ctx: Context, k: int = 1) -> frozenset[str] | None:
        if ctx.node is None or ctx.node not in self.nodes:
            return None
        return self._cache.get((ctx.node, k)) or self._reach(ctx.node, k)


class LearnedEdges:
    """Tool -> tool edges seen in train with support >= ``min_support``."""

    label = "learned edges, not workflow-defined"

    def __init__(self, min_support: int = 3) -> None:
        self.min_support = min_support
        self.edges: dict[str, frozenset[str]] = {}
        self._cache: dict[tuple[str, int], frozenset[str]] = {}

    def fit(self, train: Sequence[Episode]) -> LearnedEdges:
        counts: Counter[tuple[str, str]] = Counter()
        for ep in train:
            prev = START
            for i in range(ep.n_batches + 1):
                nxt = ep.batch_tools(i) if i < ep.n_batches else [END]
                for t in set(nxt):
                    counts[(prev, t)] += 1
                # The last tool of the batch in name order is the flat "last tool".
                prev = sorted(nxt)[-1] if i < ep.n_batches else prev
        edges: dict[str, set[str]] = {}
        for (a, b), c in counts.items():
            if c >= self.min_support:
                edges.setdefault(a, set()).add(b)
        self.edges = {a: frozenset(bs) for a, bs in edges.items()}
        # Precomputed here so prediction never writes shared state (thread safety).
        self._cache = {(a, k): self._reach(a, k) for a in self.edges for k in range(1, 6)}
        return self

    def _reach(self, last: str, k: int) -> frozenset[str]:
        frontier = {last}
        tools: set[str] = set()
        for _ in range(k):
            tools = set()
            for a in frontier:
                tools |= self.edges.get(a, frozenset())
            frontier = tools - {END}
        return frozenset(tools)

    def allowed(self, ctx: Context, k: int = 1) -> frozenset[str] | None:
        last = ctx.tools(1)
        a = last[0] if last else START
        if a not in self.edges:
            return None
        return self._cache.get((a, k)) or self._reach(a, k)
