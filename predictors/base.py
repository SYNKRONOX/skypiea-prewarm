"""Predictor and policy interfaces.

A ``Predictor`` turns a tool history into next-tool probabilities. A ``Policy``
turns that into warm decisions. The same ``Policy`` code runs in the live
controller and in the simulator.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass

from predictors.data import END, Context, Episode

__all__ = ["END", "Context", "Episode", "NextToolModel", "Policy", "Predictor",
           "ThresholdPolicy", "WarmDecision"]


@dataclass(frozen=True)
class WarmDecision:
    """Ask the controller to have ``copies`` pods ready in ``start_in_ms``."""

    copies: int
    start_in_ms: float
    prob: float


class Predictor(ABC):
    """Predicts the next tool from the tool history of a session."""

    @abstractmethod
    def fit(self, sequences: Sequence[Sequence[str]]) -> Predictor:
        """Train on a list of tool sequences. Returns ``self``."""

    @abstractmethod
    def predict_proba(self, history: Sequence[str]) -> dict[str, float]:
        """Probability of each next tool (may include ``END``)."""

    def top_k(self, history: Sequence[str], k: int) -> list[str]:
        """The ``k`` most likely next tools, ties broken by name."""
        probs = self.predict_proba(history)
        ranked = sorted(probs.items(), key=lambda kv: (-kv[1], kv[0]))
        return [t for t, _ in ranked[:k]]


class Policy(ABC):
    """Decides which tools to warm given the history so far."""

    @abstractmethod
    def decide(self, history: Sequence[str]) -> dict[str, WarmDecision]:
        """Map each tool to warm onto its decision."""


class ThresholdPolicy(Policy):
    """Warm every tool whose probability is at least ``tau``.

    This warms all likely branches, not just the top-1 tool. ``END`` is never
    warmed.
    """

    def __init__(self, predictor: Predictor, tau: float = 0.6) -> None:
        if not 0.0 <= tau <= 1.0:
            raise ValueError("tau must be in [0, 1]")
        self.predictor = predictor
        self.tau = tau

    def decide(self, history: Sequence[str]) -> dict[str, WarmDecision]:
        probs = self.predictor.predict_proba(history)
        return {
            tool: WarmDecision(copies=1, start_in_ms=0.0, prob=p)
            for tool, p in probs.items()
            if tool != END and p >= self.tau
        }


class NextToolModel(ABC):
    """A next-tool model over prediction contexts (``predictors.data``).

    ``predict(ctx, k)`` returns a distribution over the tools of the k-th next batch, which
    may include ``END``; values are >= 0 and sum to at most 1. ``batch_probs(ctx)`` returns,
    per tool, the probability that it appears in the next batch (these may sum above 1).
    Models must read the history only through ``ctx.prefix()``, ``ctx.last()`` or
    ``ctx.tools()`` (see the leakage property test).
    """

    name: str = "model"
    mean_batch_size: float = 1.0

    @abstractmethod
    def fit(self, train: Sequence[Episode], val: Sequence[Episode]) -> NextToolModel:
        """Train on ``train``; tune any hyper-parameters on ``val`` only. Returns self."""

    @abstractmethod
    def predict(self, ctx: Context, k: int = 1) -> dict[str, float]:
        """Distribution over the tools of the k-th next batch (may include ``END``)."""

    def predict_many(self, ctxs: Sequence[Context], k: int = 1) -> list[dict[str, float]]:
        return [self.predict(c, k) for c in ctxs]

    def batch_probs(self, ctx: Context) -> dict[str, float]:
        """P(tool in next batch). Default: 1 - (1 - p)^m with m the mean train batch size."""
        m = max(self.mean_batch_size, 1.0)
        return {t: 1.0 - (1.0 - p) ** m for t, p in self.predict(ctx, 1).items() if t != END}

    def batch_probs_many(self, ctxs: Sequence[Context]) -> list[dict[str, float]]:
        return [self.batch_probs(c) for c in ctxs]
