"""Global frequency baseline: ignores history and predicts by overall popularity."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

from predictors.base import Predictor


class FrequencyPredictor(Predictor):
    """Predicts each tool with its share of all calls in the training data."""

    def __init__(self) -> None:
        self.probs: dict[str, float] = {}

    def fit(self, sequences: Sequence[Sequence[str]]) -> FrequencyPredictor:
        counts = Counter(t for seq in sequences for t in seq)
        total = sum(counts.values())
        self.probs = {t: c / total for t, c in counts.items()} if total else {}
        return self

    def predict_proba(self, history: Sequence[str]) -> dict[str, float]:
        return dict(self.probs)
