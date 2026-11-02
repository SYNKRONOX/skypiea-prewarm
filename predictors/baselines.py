"""Baseline ladder: majority, frequency, Markov orders 1-3 with back-off, interpolated
n-gram, and the PRD graph-prior blend.

All models share one count structure built from TRAIN decision points: for each horizon k
and context order n (0 = no context), ``context -> Counter(tool of the k-th next batch)``.
A context of order n is the last n tools of the flat history, padded with ``<start>``.
Direct k-step tables (rather than chaining one-step predictions) give the "most frequent
continuation" at k = 3 and 5. ``END`` is a target at the end decision point, so every
distribution keeps an explicit end mass.

Hyper-parameters (interpolation weights, alpha) are tuned on VAL by negative log-likelihood
of the next call; test data is never seen in ``fit``.
"""

from __future__ import annotations

import itertools
import math
from collections import Counter
from collections.abc import Sequence

import numpy as np

from predictors.base import NextToolModel
from predictors.data import END, Context, Episode
from predictors.workflow import START, LearnedEdges, WorkflowEdges, scenario_for

HORIZONS = (1, 2, 3, 4, 5)
FLOOR = 1e-6


def context_key(ctx: Context, n: int) -> tuple[str, ...]:
    if n == 0:
        return ()
    tools = ctx.tools(n)
    return (START,) * (n - len(tools)) + tools


def training_targets(ep: Episode, i: int, k: int) -> list[str]:
    """Targets of decision point ``i`` at horizon k (END exactly at the end point)."""
    j = i + k - 1
    if j < ep.n_batches:
        return ep.batch_tools(j)
    return [END] if j == ep.n_batches else []


class CountTables:
    """``tables[(k, n)][context] = (probabilities, total count)``."""

    def __init__(self, max_order: int = 3, horizons: Sequence[int] = HORIZONS) -> None:
        self.max_order = max_order
        self.horizons = tuple(horizons)
        self.tables: dict[tuple[int, int], dict[tuple[str, ...], tuple[dict[str, float], int]]]
        self.tables = {}
        self.vocab: tuple[str, ...] = ()
        self.mean_batch_size = 1.0

    def fit(self, train: Sequence[Episode]) -> CountTables:
        raw: dict[tuple[int, int], dict[tuple[str, ...], Counter[str]]] = {
            (k, n): {} for k in self.horizons for n in range(self.max_order + 1)}
        vocab: set[str] = {END}
        n_calls = n_batches = 0
        for ep in train:
            n_batches += ep.n_batches
            n_calls += len(ep.events)
            vocab.update(e.tool for e in ep.events)
            for i in range(ep.n_batches + 1):
                ctx = ep.context(i)
                keys = [context_key(ctx, n) for n in range(self.max_order + 1)]
                for k in self.horizons:
                    tg = training_targets(ep, i, k)
                    if not tg:
                        continue
                    for n, key in enumerate(keys):
                        c = raw[(k, n)].setdefault(key, Counter())
                        c.update(tg)
        self.vocab = tuple(sorted(vocab))
        self.mean_batch_size = n_calls / max(n_batches, 1)
        self.tables = {}
        for kn, table in raw.items():
            self.tables[kn] = {}
            for key, c in table.items():
                total = sum(c.values())
                self.tables[kn][key] = ({t: v / total for t, v in c.items()}, total)
        return self

    def lookup(self, ctx: Context, k: int, n: int) -> tuple[dict[str, float], int] | None:
        return self.tables[(k, n)].get(context_key(ctx, n))

    def order0(self, k: int) -> dict[str, float]:
        """Unsmoothed order-0 distribution. If train never reached horizon k (e.g. sessions
        shorter than k calls), the k-th next call was never observed: all mass on END."""
        hit = self.tables.get((k, 0), {}).get(())
        return dict(hit[0]) if hit else {END: 1.0}

    def unigram(self, k: int) -> dict[str, float]:
        """Order-0 distribution, add-one smoothed over the train vocabulary (never zero)."""
        probs, total = self.tables.get((k, 0), {}).get((), ({}, 0))
        if total == 0:
            return {END: 1.0}
        v = len(self.vocab)
        return {t: (probs.get(t, 0.0) * total + 1.0) / (total + v) for t in self.vocab}


def _k(k: int, tables: CountTables) -> int:
    return k if k in tables.horizons else max(tables.horizons)


class _CountModel(NextToolModel):
    max_order = 0

    def __init__(self) -> None:
        self.counts = CountTables(self.max_order)

    def fit(self, train: Sequence[Episode], val: Sequence[Episode]) -> _CountModel:
        self.counts = CountTables(self.max_order).fit(train)
        self.mean_batch_size = self.counts.mean_batch_size
        return self


class Majority(_CountModel):
    """Always the single most frequent next tool (all mass on it)."""

    name = "majority"

    def fit(self, train: Sequence[Episode], val: Sequence[Episode]) -> Majority:
        super().fit(train, val)
        probs = self.counts.order0(1)
        self.label = min((t for t in probs if t != END), key=lambda t: (-probs[t], t))
        return self

    def predict(self, ctx: Context, k: int = 1) -> dict[str, float]:
        return {self.label: 1.0}


class Frequency(_CountModel):
    """Overall frequency of the k-th next tool, ignoring history."""

    name = "frequency"

    def predict(self, ctx: Context, k: int = 1) -> dict[str, float]:
        return self.counts.order0(_k(k, self.counts))


class Markov(_CountModel):
    """Order-n Markov with back-off to the longest context seen at least ``min_count`` times
    (down to order 0). Order 1 is the PRD MVP."""

    def __init__(self, order: int = 1, min_count: int = 1) -> None:
        self.max_order = order
        self.min_count = min_count
        self.name = f"markov{order}"
        super().__init__()

    def predict(self, ctx: Context, k: int = 1) -> dict[str, float]:
        k = _k(k, self.counts)
        for n in range(self.max_order, -1, -1):
            hit = self.counts.lookup(ctx, k, n)
            if hit is not None and hit[1] >= self.min_count:
                return dict(hit[0])
        return self.counts.unigram(k)


def _simplex(m: int, step: float) -> list[tuple[float, ...]]:
    n = round(1 / step)
    return [tuple(c / n for c in combo) for combo in itertools.product(range(n + 1), repeat=m)
            if sum(combo) == n]


class Interpolated(_CountModel):
    """sum_n lambda_n P_n(j | last n tools), n = 0..3, with smoothed P_0. When a context is
    unseen its weight is spread over the remaining orders. Weights tuned on val (NLL)."""

    name = "ngram"

    def __init__(self, max_order: int = 3, step: float = 0.1) -> None:
        self.max_order = max_order
        self.step = step
        self.weights: tuple[float, ...] = (0.1,) + (0.9 / max_order,) * max_order
        super().__init__()

    def _components(self, ctx: Context, k: int) -> list[dict[str, float] | None]:
        comps: list[dict[str, float] | None] = [self.counts.unigram(k)]
        for n in range(1, self.max_order + 1):
            hit = self.counts.lookup(ctx, k, n)
            comps.append(hit[0] if hit else None)
        return comps

    def _mix(self, comps: list[dict[str, float] | None]) -> dict[str, float]:
        wsum = sum(w for w, c in zip(self.weights, comps, strict=True) if c is not None)
        if wsum <= 0:
            return dict(comps[0] or {})
        out: dict[str, float] = {}
        for w, c in zip(self.weights, comps, strict=True):
            if c is None or w == 0:
                continue
            for t, p in c.items():
                out[t] = out.get(t, 0.0) + w * p / wsum
        return out

    def fit(self, train: Sequence[Episode], val: Sequence[Episode]) -> Interpolated:
        super().fit(train, val)
        rows = []
        for ep in val:
            for i in range(ep.n_batches):
                comps = self._components(ep.context(i), 1)
                for t in ep.batch_tools(i):
                    rows.append([math.nan if c is None else c.get(t, 0.0) for c in comps])
        if rows:
            p = np.asarray(rows)
            avail = ~np.isnan(p)
            p = np.nan_to_num(p)
            best, best_nll = self.weights, math.inf
            for w in _simplex(self.max_order + 1, self.step):
                wv = np.asarray(w)
                wsum = avail @ wv
                mix = np.where(wsum > 0, (p @ wv) / np.where(wsum > 0, wsum, 1), p[:, 0])
                nll = -np.mean(np.log(np.maximum(mix, FLOOR)))
                if nll < best_nll - 1e-12:
                    best, best_nll = w, nll
            self.weights = best
        return self

    def predict(self, ctx: Context, k: int = 1) -> dict[str, float]:
        return self._mix(self._components(ctx, _k(k, self.counts)))


class GraphBlend(NextToolModel):
    """PRD predictor: score(j) = alpha * graph_prior(j) + (1 - alpha) * P(j | history).

    graph_prior is uniform over the allowed next tools (renormalised): workflow-defined edges
    when the source has a workflow graph and the node is known, otherwise edges learned from
    train (support >= ``min_support``). If no edge information applies (unseen node or tool)
    the prior is off and the base model is used alone. alpha is tuned on val (NLL).
    ``base="markov1"`` is the PRD form; ``base="ngram"`` blends the prior into the
    interpolated n-gram.
    """

    def __init__(self, base: str = "markov1", min_support: int = 3,
                 alphas: Sequence[float] = tuple(i / 20 for i in range(21))) -> None:
        self.base_name = base
        self.min_support = min_support
        self.alphas = tuple(alphas)
        self.alpha = 0.0
        self.name = "graph_blend" if base == "markov1" else f"graph_blend_{base}"
        self.base: NextToolModel = Markov(1) if base == "markov1" else Interpolated()
        self.edges: WorkflowEdges | LearnedEdges = LearnedEdges(min_support)

    @property
    def edge_label(self) -> str:
        return self.edges.label

    def _prior(self, ctx: Context, k: int) -> dict[str, float] | None:
        allowed = self.edges.allowed(ctx, k)
        if not allowed:
            return None
        u = 1.0 / len(allowed)
        return {t: u for t in allowed}

    @staticmethod
    def _blend(alpha: float, prior: dict[str, float] | None,
               base: dict[str, float]) -> dict[str, float]:
        if prior is None or alpha == 0.0:
            return dict(base)
        out = {t: (1 - alpha) * p for t, p in base.items()}
        for t, p in prior.items():
            out[t] = out.get(t, 0.0) + alpha * p
        total = sum(out.values())
        return {t: p / total for t, p in out.items()} if total > 0 else dict(base)

    def fit(self, train: Sequence[Episode], val: Sequence[Episode]) -> GraphBlend:
        self.base.fit(train, val)
        self.mean_batch_size = self.base.mean_batch_size
        scenario = scenario_for(train[0].source) if train else None
        self.edges = (WorkflowEdges(scenario) if scenario
                      else LearnedEdges(self.min_support).fit(train))
        pts = [(ep.context(i), ep.batch_tools(i)) for ep in val for i in range(ep.n_batches)]
        if pts:
            cached = [(self._prior(c, 1), self.base.predict(c, 1), tg) for c, tg in pts]
            best, best_nll = 0.0, math.inf
            for a in self.alphas:
                nll = 0.0
                for prior, base, tg in cached:
                    d = self._blend(a, prior, base)
                    nll -= sum(math.log(max(d.get(t, 0.0), FLOOR)) for t in tg)
                if nll < best_nll - 1e-9:
                    best, best_nll = a, nll
            self.alpha = best
        return self

    def predict(self, ctx: Context, k: int = 1) -> dict[str, float]:
        return self._blend(self.alpha, self._prior(ctx, k), self.base.predict(ctx, k))


def ladder() -> list[NextToolModel]:
    """Fresh, unfitted instances of the Phase 2 baselines."""
    return [Majority(), Frequency(), Markov(1), Markov(2), Markov(3), Interpolated(),
            GraphBlend("markov1"), GraphBlend("ngram")]
