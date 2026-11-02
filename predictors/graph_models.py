"""Models computed from a TransitionGraph's count tables, so that one counts.json (plus a
few tuned weights) defines everything the ranker and the service need.

- ``GraphNgram``: interpolated n-gram sum_n lambda_n P_n(next | last n tools), n = 0..3, over
  the graph's (guarded) h1 tables; P_0 add-one smoothed; weights tuned on val by NLL.
- ``GraphPriorBlend``: the PRD predictor alpha * graph_prior + (1 - alpha) * P(j | last tool),
  with graph_prior uniform over the allowed next tools: workflow edges when the scenario is
  known and the node is set, otherwise "learned edges, not workflow-defined" = the order-1
  transitions kept in the graph (support >= min_support). alpha tuned on val by NLL.

Both are read-only after fitting and serialise to small dicts (``params()``).
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Sequence
from typing import Any

import numpy as np

from predictors.base import NextToolModel
from predictors.data import END, Context, Episode
from predictors.graph import TransitionGraph, _normalise, context_keys
from predictors.workflow import WorkflowEdges, scenario_for

FLOOR = 1e-6


def _simplex(m: int, step: float) -> list[tuple[float, ...]]:
    n = round(1 / step)
    return [tuple(c / n for c in combo) for combo in itertools.product(range(n + 1), repeat=m)
            if sum(combo) == n]


def _val_points(val: Sequence[Episode]) -> list[tuple[Context, list[str]]]:
    return [(ep.context(i), ep.batch_tools(i)) for ep in val for i in range(ep.n_batches)]


class GraphNgram(NextToolModel):
    name = "graph_ngram"

    def __init__(self, graph: TransitionGraph, weights: Sequence[float] | None = None,
                 step: float = 0.1) -> None:
        self.graph = graph
        self.step = step
        m = graph.max_order + 1
        self.weights = tuple(weights) if weights is not None else (1.0 / m,) * m
        self.mean_batch_size = graph.mean_batch_size
        vocab = graph.vocab
        uni = graph.tables.get("h1", {}).get("", {})
        total = sum(uni.values())
        self._p0 = {t: (uni.get(t, 0.0) + 1.0) / (total + len(vocab)) for t in vocab} \
            if vocab else {END: 1.0}

    def _components(self, ctx: Context) -> list[dict[str, float] | None]:
        table = self.graph.tables.get("h1", {})
        tot = self.graph.totals.get("h1", {})
        keys = context_keys(ctx, self.graph.max_order)
        comps: list[dict[str, float] | None] = [self._p0]
        for key in keys[1:]:
            c = table.get(key)
            comps.append(_normalise(c) if c and tot.get(key, 0) >= self.graph.min_support
                         else None)
        return comps

    def fit(self, train: Sequence[Episode], val: Sequence[Episode]) -> GraphNgram:
        """``train`` is ignored (the graph is already fitted); weights are tuned on val."""
        rows = []
        for ctx, tools in _val_points(val):
            comps = self._components(ctx)
            for t in tools:
                rows.append([math.nan if c is None else c.get(t, 0.0) for c in comps])
        if rows:
            p = np.asarray(rows)
            avail = ~np.isnan(p)
            p = np.nan_to_num(p)
            best, best_nll = self.weights, math.inf
            for w in _simplex(p.shape[1], self.step):
                wv = np.asarray(w)
                wsum = avail @ wv
                mix = np.where(wsum > 0, (p @ wv) / np.where(wsum > 0, wsum, 1), p[:, 0])
                nll = float(-np.mean(np.log(np.maximum(mix, FLOOR))))
                if nll < best_nll - 1e-12:
                    best, best_nll = w, nll
            self.weights = best
        return self

    def predict(self, ctx: Context, k: int = 1) -> dict[str, float]:
        if k != 1:
            return self.graph.predict(ctx, k)
        comps = self._components(ctx)
        wsum = sum(w for w, c in zip(self.weights, comps, strict=True) if c is not None)
        if wsum <= 0:
            return dict(self._p0)
        out: dict[str, float] = {}
        for w, c in zip(self.weights, comps, strict=True):
            if c is None or w == 0:
                continue
            for t, v in c.items():
                out[t] = out.get(t, 0.0) + w * v / wsum
        return out

    def params(self) -> dict[str, Any]:
        return {"weights": list(self.weights)}


class GraphPriorBlend(NextToolModel):
    name = "graph_prior_blend"

    def __init__(self, graph: TransitionGraph, scenario: str | None = None, alpha: float = 0.0,
                 alphas: Sequence[float] = tuple(i / 20 for i in range(21))) -> None:
        self.graph = graph
        self.scenario = scenario
        self.alpha = alpha
        self.alphas = tuple(alphas)
        self.mean_batch_size = graph.mean_batch_size
        self.workflow = WorkflowEdges(scenario) if scenario else None
        h1 = graph.tables.get("h1", {})
        self._learned = {key: frozenset(c) for key, c in h1.items()
                         if key and "|" not in key}  # order-1 keys: the last tool

    @property
    def edge_label(self) -> str:
        return (self.workflow.label if self.workflow is not None
                else "learned edges, not workflow-defined")

    def allowed(self, ctx: Context) -> frozenset[str] | None:
        if self.workflow is not None:
            a = self.workflow.allowed(ctx, 1)
            if a is not None:
                return a
        key = context_keys(ctx, 1)[1]
        return self._learned.get(key)

    def _markov1(self, ctx: Context) -> dict[str, float]:
        table, tot = self.graph.tables.get("h1", {}), self.graph.totals.get("h1", {})
        for key in reversed(context_keys(ctx, 1)):
            c = table.get(key)
            if c and tot.get(key, 0) >= self.graph.min_support:
                return _normalise(c)
        return {END: 1.0}

    @staticmethod
    def _blend(alpha: float, allowed: frozenset[str] | None,
               base: dict[str, float]) -> dict[str, float]:
        if not allowed or alpha == 0.0:
            return dict(base)
        u = 1.0 / len(allowed)
        out = {t: (1 - alpha) * p for t, p in base.items()}
        for t in allowed:
            out[t] = out.get(t, 0.0) + alpha * u
        return _normalise(out)

    def fit(self, train: Sequence[Episode], val: Sequence[Episode]) -> GraphPriorBlend:
        if self.scenario is None and train:
            sc = scenario_for(train[0].source)
            if sc:
                self.scenario, self.workflow = sc, WorkflowEdges(sc)
        cached = [(self.allowed(c), self._markov1(c), tg) for c, tg in _val_points(val)]
        if cached:
            best, best_nll = 0.0, math.inf
            for a in self.alphas:
                nll = 0.0
                for allowed, base, tg in cached:
                    d = self._blend(a, allowed, base)
                    nll -= sum(math.log(max(d.get(t, 0.0), FLOOR)) for t in tg)
                if nll < best_nll - 1e-9:
                    best, best_nll = a, nll
            self.alpha = best
        return self

    def predict(self, ctx: Context, k: int = 1) -> dict[str, float]:
        if k != 1:
            return self.graph.predict(ctx, k)
        return self._blend(self.alpha, self.allowed(ctx), self._markov1(ctx))

    def params(self) -> dict[str, Any]:
        return {"alpha": self.alpha, "scenario": self.scenario}
