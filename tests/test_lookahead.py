"""lookahead=2 (feat/console-leadtime): off by default; warms the most likely tool of the
batch after next only if new and above the threshold."""

from __future__ import annotations

import pytest

from predictors.decide import DecideConfig
from tests.test_decide import CTX, _decider, _Graph, cold  # noqa: F401 - fixture


class _Graph2(_Graph):
    def __init__(self, p2: dict[str, float]) -> None:
        super().__init__()
        self.p2 = p2

    def predict(self, ctx, k: int = 1):  # noqa: ANN001, ANN201
        assert k == 2
        return self.p2


def test_default_is_one_and_shipped_profiles_unchanged() -> None:
    assert DecideConfig().lookahead == 1
    for p in ("support", "coding", "prd"):
        assert DecideConfig.for_profile(p).lookahead == 1
    with pytest.raises(ValueError):
        DecideConfig.load(lookahead=3)


def test_lookahead_one_never_reads_two_steps(cold) -> None:  # noqa: F811
    d = _decider(cold, {"shell": 0.7}, graph=_Graph2({"git": 0.9})).decide(CTX)
    assert set(d.warm) == {"shell"}


def test_lookahead_two_adds_the_top_two_step_tool_above_threshold(cold) -> None:  # noqa: F811
    d = _decider(cold, {"shell": 0.7}, graph=_Graph2({"git": 0.8, "other": 0.65,
                                                      "<end>": 0.1}),
                 lookahead=2).decide(CTX)
    assert set(d.warm) == {"shell", "git"}
    g = d.warm["git"]
    assert g.prob == 0.8 and g.copies == 1 and g.start_in_ms == 0.0


def test_lookahead_two_respects_threshold_and_skips_already_warmed(cold) -> None:  # noqa: F811
    low = _decider(cold, {"shell": 0.7}, graph=_Graph2({"git": 0.5}), lookahead=2)
    assert set(low.decide(CTX).warm) == {"shell"}            # 0.5 < tau 0.6
    same = _decider(cold, {"shell": 0.7}, graph=_Graph2({"shell": 0.9, "git": 0.61}),
                    lookahead=2)
    assert set(same.decide(CTX).warm) == {"shell", "git"}    # shell already warmed
    abst = _decider(cold, {"shell": 0.2}, graph=_Graph2({"git": 0.9}), lookahead=2)
    assert abst.decide(CTX).warm == {}                       # step 1 abstains: nothing
