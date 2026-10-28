"""Predictor and policy interfaces.

A ``Predictor`` turns a tool history into next-tool probabilities. A ``Policy``
turns that into warm decisions. The same ``Policy`` code runs in the live
controller and in the simulator.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass

END = "<end>"
"""Pseudo-tool marking the end of a session."""


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
