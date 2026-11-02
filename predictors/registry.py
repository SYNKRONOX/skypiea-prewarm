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

    return ladder()


def feature_builders(train: Sequence[Episode]) -> list[Callable[[Context], Any]]:
    """Feature builders fitted on ``train``; each maps a context to comparable features."""
    return []
