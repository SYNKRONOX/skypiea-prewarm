"""Leakage property test: what a model or feature builder computes at position t depends only
on events[:t] (and the node), never on events[t:].

For random sessions and positions t we replace events[t:] with random junk (other tools,
absurd times, errors) and check that predictions at every horizon, batch probabilities and
features are identical. Every model and feature builder in ``predictors.registry`` is
checked, so later phases are covered automatically once they register.
"""

from __future__ import annotations

import random

import pytest

from predictors.data import Context, Event, episodes
from predictors.eval import KS
from predictors.registry import feature_builders, models
from traces.load import SAMPLES, load_sessions

SOURCES = ["tau_bench", "tracelab"]
N_CASES = 40


def _junk(rng: random.Random, n: int, tools: list[str]) -> list[Event]:
    return [Event(rng.choice(tools + ["zz_junk"]), rng.choice([None, 1e12, -5.0, 3.0]),
                  rng.randrange(10**6), rng.choice([None, 1e9, 0.0]), rng.random() < 0.5)
            for _ in range(n)]


def _cases(source: str) -> list[tuple[Context, Context]]:
    rng = random.Random(0)
    eps = episodes(load_sessions(source, "test", root=SAMPLES))
    tools = sorted({e.tool for ep in eps for e in ep.events})
    out = []
    for _ in range(N_CASES):
        ep = rng.choice(eps)
        i = rng.randrange(ep.n_batches + 1)
        ctx = ep.context(i)
        t = ctx.upto
        junked = list(ep.events[:t]) + _junk(rng, rng.randrange(1, 30), tools)
        out.append((Context(tuple(ep.events[:t]), t, ctx.node),
                    Context(tuple(junked), t, ctx.node)))
    return out


@pytest.fixture(scope="module", params=SOURCES)
def fitted(request: pytest.FixtureRequest) -> tuple[str, list]:
    source = request.param
    train = episodes(load_sessions(source, "train", root=SAMPLES))
    val = episodes(load_sessions(source, "val", root=SAMPLES))
    return source, [m.fit(train, val) for m in models()]


def test_models_ignore_the_future(fitted: tuple[str, list]) -> None:
    source, fitted_models = fitted
    for clean, junked in _cases(source):
        for m in fitted_models:
            for k in KS:
                assert m.predict(clean, k) == m.predict(junked, k), (m.name, k)
            assert m.batch_probs(clean) == m.batch_probs(junked), m.name


def test_feature_builders_ignore_the_future(fitted: tuple[str, list]) -> None:
    source, fitted_models = fitted
    train = episodes(load_sessions(source, "train", root=SAMPLES))
    for build in feature_builders(train):
        for clean, junked in _cases(source):
            assert build(clean) == build(junked), getattr(build, "__name__", build)


def test_the_check_detects_a_leaky_model() -> None:
    """A model that peeks past ``upto`` must be caught by the same comparison."""

    def leaky(ctx: Context) -> int:
        return len(ctx.events)  # reads the future

    leaks = [leaky(c) != leaky(j) for c, j in _cases("tau_bench")]
    assert any(leaks)
