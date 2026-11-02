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

    return [*ladder(), TransitionGraph()]


def feature_builders(train: Sequence[Episode]) -> list[Callable[[Context], Any]]:
    """Feature builders fitted on ``train``; each maps a context to comparable features."""
    from predictors.graph import TransitionGraph, graph_features

    g = TransitionGraph().fit(train)

    def transition_graph_features(ctx: Context) -> Any:
        return graph_features(g, ctx)

    return [transition_graph_features]
