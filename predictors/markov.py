"""Order-n Markov predictor over tool sequences, with back-off.

Each sequence is padded with ``order`` start tokens and closed with ``END``.
At prediction time the model uses the longest context (order n down to 1)
seen at least ``min_count`` times, then falls back to unigram frequencies.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence

from predictors.base import END, Predictor

START = "<start>"


class MarkovPredictor(Predictor):
    def __init__(self, order: int = 1, min_count: int = 1) -> None:
        if order < 1:
            raise ValueError("order must be >= 1")
        self.order = order
        self.min_count = min_count
        # counts[n][context] -> Counter of next tools, for context length n.
        self.counts: dict[int, dict[tuple[str, ...], Counter[str]]] = {}
        self.unigram: dict[str, float] = {}

    def fit(self, sequences: Sequence[Sequence[str]]) -> MarkovPredictor:
        self.counts = {n: defaultdict(Counter) for n in range(1, self.order + 1)}
        uni: Counter[str] = Counter()
        for seq in sequences:
            padded = [START] * self.order + list(seq) + [END]
            for i in range(self.order, len(padded)):
                nxt = padded[i]
                uni[nxt] += 1
                for n in range(1, self.order + 1):
                    self.counts[n][tuple(padded[i - n : i])][nxt] += 1
        total = sum(uni.values())
        self.unigram = {t: c / total for t, c in uni.items()} if total else {}
        return self

    def predict_proba(self, history: Sequence[str]) -> dict[str, float]:
        padded = [START] * self.order + list(history)
        for n in range(self.order, 0, -1):
            ctx = tuple(padded[len(padded) - n :])
            nxt = self.counts.get(n, {}).get(ctx)
            if nxt:
                total = sum(nxt.values())
                if total >= self.min_count:
                    return {t: c / total for t, c in nxt.items()}
        return dict(self.unigram)

    def k_step(self, history: Sequence[str], k: int) -> dict[str, float]:
        """Probability that each tool appears at least once in the next ``k`` steps.

        Computed exactly by expanding all paths up to depth ``k``. ``END`` is
        absorbing, so paths that end early stop contributing new tools.
        """
        result: dict[str, float] = defaultdict(float)
        # Frontier entries: (history, path probability, tools already seen on path).
        frontier: list[tuple[list[str], float, frozenset[str]]] = [
            (list(history), 1.0, frozenset())
        ]
        for _ in range(k):
            nxt_frontier: list[tuple[list[str], float, frozenset[str]]] = []
            for hist, p, seen in frontier:
                for tool, q in self.predict_proba(hist).items():
                    pq = p * q
                    if tool == END:
                        continue
                    if tool not in seen:
                        result[tool] += pq
                    nxt_frontier.append((hist + [tool], pq, seen | {tool}))
            frontier = nxt_frontier
        return dict(result)
