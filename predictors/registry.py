"""Every model and feature builder, so property tests (leakage, robustness) cover them all.

Later phases add their entries here.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from predictors.base import NextToolModel
from predictors.data import Context, Episode


def models() -> list[NextToolModel]:
    """Fresh, unfitted instances of every next-tool model."""
    from predictors.baselines import ladder
    from predictors.graph import TransitionGraph
    from predictors.lgbm_ranker import LGBMRanker

    return [*ladder(), TransitionGraph(), LGBMRanker.from_config()]


def feature_builders(train: Sequence[Episode]) -> list[Callable[[Context], Any]]:
    """Feature builders fitted on ``train``; each maps a context to comparable features."""
    from predictors.graph import TransitionGraph, graph_features
    from predictors.lgbm_ranker import LGBMRanker

    g = TransitionGraph().fit(train)
    r = LGBMRanker.from_config().fit(train, train)

    def transition_graph_features(ctx: Context) -> Any:
        return graph_features(g, ctx)

    def ranker_rows(ctx: Context) -> Any:
        assert r.base is not None
        cands, x = r.rows(r.base, ctx)
        return cands, x.tolist()

    return [transition_graph_features, ranker_rows]
