"""Leak-free splitting and top-k accuracy for next-tool predictors."""

from __future__ import annotations

import random
from collections.abc import Iterable, Sequence

from predictors.base import Predictor
from traces.schema import Session


def group_split(
    sessions: Sequence[Session], test_frac: float = 0.3, seed: int = 0
) -> tuple[list[Session], list[Session]]:
    """Split sessions so that no ``group`` (task or user) is in both halves."""
    groups = sorted({s.group for s in sessions})
    rng = random.Random(seed)
    rng.shuffle(groups)
    n_test = round(len(groups) * test_frac)
    test_groups = set(groups[:n_test])
    train = [s for s in sessions if s.group not in test_groups]
    test = [s for s in sessions if s.group in test_groups]
    return train, test


def topk_accuracy(
    model: Predictor,
    sequences: Iterable[Sequence[str]],
    ks: Sequence[int] = (1, 3),
    ahead: int = 1,
) -> dict[int, float]:
    """Fraction of positions where the tool ``ahead`` steps later is in the top-k.

    With ``ahead=1`` this is ordinary next-tool accuracy. For ``ahead > 1`` the
    model is still asked for its next-step ranking, which measures how well a
    one-step model anticipates further ahead.
    """
    hits = {k: 0 for k in ks}
    n = 0
    max_k = max(ks)
    for seq in sequences:
        for i in range(len(seq) - ahead + 1):
            target = seq[i + ahead - 1]
            ranked = model.top_k(seq[:i], max_k)
            n += 1
            for k in ks:
                if target in ranked[:k]:
                    hits[k] += 1
    return {k: (hits[k] / n if n else 0.0) for k in ks}
