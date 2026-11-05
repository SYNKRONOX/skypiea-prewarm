"""Fail-open model ladder: ranker -> n-gram -> majority -> none (FR-4).

    lad = Ladder([("ranker", r), ("ngram", r.base.ngram), ("majority", maj)], budget_ms=40)
    probs, mode, degraded = lad.batch_probs(ctx)

Each rung is tried in order. A rung that raises, returns non-finite or out-of-range values,
or takes longer than ``budget_ms`` is skipped (its answer is discarded) and the next rung
answers with ``degraded=True``. Python cannot pre-empt a running call, so the budget is
checked when the rung returns; a slow rung still costs its own time once. If every rung
fails the answer is ``({}, "none", True)``: no decisions, the normal cold path.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Sequence

from predictors.base import NextToolModel
from predictors.data import Context


def _valid(d: dict[str, float]) -> bool:
    return isinstance(d, dict) and all(
        isinstance(v, float | int) and math.isfinite(v) and 0.0 <= v <= 1.0 + 1e-9
        for v in d.values())


class Ladder:
    def __init__(self, rungs: Sequence[tuple[str, NextToolModel]], budget_ms: float = 40.0,
                 clock: Callable[[], float] = time.perf_counter) -> None:
        if not rungs:
            raise ValueError("a ladder needs at least one rung")
        self.rungs = list(rungs)
        self.budget_ms = budget_ms
        self.clock = clock

    def _run(self, fn: str, ctx: Context) -> tuple[dict[str, float], str, bool]:
        for i, (name, model) in enumerate(self.rungs):
            t0 = self.clock()
            try:
                out = getattr(model, fn)(ctx)
            except Exception:  # noqa: BLE001 - fail open on any model error
                continue
            if (self.clock() - t0) * 1000.0 > self.budget_ms or not _valid(out):
                continue
            return out, name, i > 0
        return {}, "none", True

    def batch_probs(self, ctx: Context) -> tuple[dict[str, float], str, bool]:
        """P(tool in the next batch) from the first healthy rung."""
        return self._run("batch_probs", ctx)

    def ranked(self, ctx: Context) -> tuple[dict[str, float], str, bool]:
        """FR-1 next-tool distribution (incl. END) from the first healthy rung."""
        return self._run("predict", ctx)
