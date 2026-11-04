"""Cold-start file loading, the fail-open ladder, warm decisions and the offline simulator."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from predictors.base import NextToolModel, WarmDecision
from predictors.cold_start import ColdStarts
from predictors.data import Context, Episode, Event
from predictors.decide import (
    DecideConfig,
    Decider,
    apply_caps,
    last_batch_tool,
    load_pricing,
    merge_by_server,
)
from predictors.graph import Timing
from predictors.ladder import Ladder
from sim.offline import Env, Spec, Stats, load_config, simulate


def _server(tier: str, classes: list[str], cached: float, uncached: float) -> dict:
    def mode(m: float) -> dict:
        return {"n": 10, "median_ms": m, "p95_ms": m + 500.0}
    return {"tier": tier, "classes": classes, "cached": mode(cached), "uncached": mode(uncached)}


def _cold_file(path: Path, read_ms: float = 2000.0) -> Path:
    d = {"schema_version": 1, "environment": "test", "status": "fixture", "fallback": "generic",
         "servers": {"read": _server("light", ["read_file"], read_ms, read_ms + 1000.0),
                     "shell": _server("light", ["shell", "git"], 2000.0, 3000.0),
                     "generic": _server("heavy", ["other"], 4000.0, 6000.0)}}
    path.write_text(yaml.safe_dump(d))
    return path


@pytest.fixture
def cold(tmp_path: Path) -> ColdStarts:
    return ColdStarts.load(_cold_file(tmp_path / "cold.yaml"))


# --------------------------------------------------------------------------- cold starts
def test_cold_start_mapping_and_reload(tmp_path: Path) -> None:
    p = _cold_file(tmp_path / "c.yaml")
    cs = ColdStarts.load(p)
    assert cs.server("git") == "shell" and cs.server("never_seen") == "generic"
    assert cs.ms("read_file", "cached") == 2000.0
    assert cs.ms("read_file", "uncached", "p95_ms") == 3500.0
    assert cs.tier("other") == "heavy" and cs.label == "test: fixture"
    _cold_file(p, read_ms=2500.0)  # a new file at the same path replaces every value
    cs2 = ColdStarts.load(p)
    assert cs2.ms("read_file") == 2500.0 and cs2.sha256 != cs.sha256


def test_cold_start_rejects_bad_files(tmp_path: Path) -> None:
    p = _cold_file(tmp_path / "c.yaml")
    d = yaml.safe_load(p.read_text())
    del d["servers"]["read"]["cached"]["p95_ms"]
    p.write_text(yaml.safe_dump(d))
    with pytest.raises(ValueError):
        ColdStarts.load(p)
    d = yaml.safe_load(_cold_file(p).read_text())
    d["fallback"] = "missing"
    p.write_text(yaml.safe_dump(d))
    with pytest.raises(ValueError):
        ColdStarts.load(p)


# --------------------------------------------------------------------------- ladder
class _Fixed(NextToolModel):
    def __init__(self, probs: dict[str, float] | None, fail: bool = False) -> None:
        self.probs, self.fail = probs, fail

    def fit(self, train, val):  # noqa: ANN001, ANN201
        return self

    def predict(self, ctx: Context, k: int = 1) -> dict[str, float]:
        if self.fail:
            raise RuntimeError("boom")
        assert self.probs is not None
        return self.probs

    def batch_probs(self, ctx: Context) -> dict[str, float]:
        return self.predict(ctx)


CTX = Context.of([Event("read_file", 0.0, 0, 10.0)])


def test_ladder_falls_through_on_errors_bad_values_and_budget() -> None:
    good = _Fixed({"shell": 0.7})
    assert Ladder([("ranker", good)]).batch_probs(CTX) == ({"shell": 0.7}, "ranker", False)
    lad = Ladder([("ranker", _Fixed(None, fail=True)), ("ngram", _Fixed({"x": float("nan")})),
                  ("majority", good)])
    assert lad.batch_probs(CTX) == ({"shell": 0.7}, "majority", True)
    ticks = iter([0.0, 0.05, 0.05, 0.051])  # first rung takes 50 ms, second 1 ms
    slow = Ladder([("ranker", good), ("ngram", _Fixed({"read_file": 0.9}))], budget_ms=40,
                  clock=lambda: next(ticks))
    assert slow.batch_probs(CTX) == ({"read_file": 0.9}, "ngram", True)
    assert Ladder([("ranker", _Fixed(None, fail=True))]).ranked(CTX) == ({}, "none", True)


# --------------------------------------------------------------------------- decisions
class _Graph:
    vocab = ("read_file", "shell", "git", "other")

    def __init__(self, p50: float | None = 10_000.0, p90: float | None = 20_000.0) -> None:
        self.p50, self.p90 = p50, p90

    def timing(self, prev: str, nxt: str) -> Timing:
        return Timing(None if self.p50 is None else 1.0, self.p50, self.p90, 10, "pair")

    def copies(self, tool: str, ctx: Context | None = None) -> dict[int, float]:
        return {1: 0.5, 2: 0.35, 3: 0.1, 4: 0.05}


def _decider(cold: ColdStarts, probs: dict[str, float], graph: _Graph | None = None,
             **cfg: object) -> Decider:
    base = {"tau": 0.6, "gap_stat": "p50", "plan_stat": "p95_ms", "keep_after_use_s": 30}
    c = DecideConfig.load(**{**base, **cfg})  # rules tested against fixed values
    return Decider(Ladder([("ranker", _Fixed(probs))]), graph or _Graph(), cold, c,
                   load_pricing())


def test_threshold_abstain_and_unseen(cold: ColdStarts) -> None:
    d = _decider(cold, {"shell": 0.7, "git": 0.4, "<end>": 0.2}).decide(CTX)
    assert d.mode == "ranker" and set(d.warm) == {"shell"} and d.warm["shell"].prob == 0.7
    assert _decider(cold, {"shell": 0.25}).decide(CTX).mode == "prior"
    unseen = Context.of([Event("brand_new", 0.0, 0, 1.0)])
    assert _decider(cold, {"shell": 0.9}).decide(unseen).warm == {}
    assert _decider(cold, {"shell": 0.9}).decide(unseen).mode == "prior"


def test_timing_copies_and_ttl_rules(cold: ColdStarts) -> None:
    pol = _decider(cold, {"shell": 0.9})
    w = pol.decide(CTX).warm["shell"]
    plan = cold.ms("shell", "cached", "p95_ms")  # 2,500 ms in the fixture
    assert w.start_in_ms == 10_000.0 - plan
    assert w.ttl_ms == 20_000.0 - (10_000.0 - plan) - plan  # until the p90 arrival
    assert w.copies == 3  # P(<=1) = .5, P(<=2) = .85, P(<=3) = .95 >= .9
    short = _decider(cold, {"shell": 0.9}, _Graph(1_000.0, 2_000.0)).decide(CTX).warm["shell"]
    assert short.start_in_ms == 0.0 and short.ttl_ms == 5_000.0  # ttl floor
    none = _decider(cold, {"shell": 0.9}, _Graph(None, None)).decide(CTX).warm["shell"]
    assert (none.start_in_ms, none.ttl_ms) == (0.0, 30_000.0)


def test_cost_based_threshold(cold: ColdStarts) -> None:
    # tau = (cold + ttl) / (cold + ttl + lambda * cold); shell: cold 2 s, ttl 30 s
    pol = _decider(cold, {"shell": 0.9}, threshold="cost", lambda_pod_s=10)
    assert pol.tau("shell", 30_000.0) == pytest.approx(32 / 52)
    assert _decider(cold, {}, threshold="cost", lambda_pod_s=1).tau("shell", 30_000.0) == \
        pytest.approx(32 / 34)
    assert _decider(cold, {}, threshold="cost", lambda_pod_s=1e6).tau("shell", 0.0) == 0.05
    assert _decider(cold, {"shell": 0.7}, threshold="cost", lambda_pod_s=10).decide(CTX).warm
    assert not _decider(cold, {"shell": 0.7}, threshold="cost", lambda_pod_s=1).decide(CTX).warm


def test_start_rules_and_copies_rules(cold: ColdStarts) -> None:
    g = _Graph(10_000.0, 20_000.0)
    now = _decider(cold, {"shell": 0.9}, g, start_rule="now").decide(CTX).warm["shell"]
    assert (now.start_in_ms, now.ttl_ms) == (0.0, 30_000.0)
    jit = _decider(cold, {"shell": 0.9}, g, plan_stat="median_ms").decide(CTX).warm["shell"]
    assert jit.start_in_ms == 10_000.0 - 2_000.0  # gap p50 minus the median cold start
    p10 = _decider(cold, {"shell": 0.9}, g, gap_stat="p10").decide(CTX).warm["shell"]
    assert p10.start_in_ms == 0.0  # the fixture's p10 is 1 ms
    assert _decider(cold, {"shell": 0.9}, g, copies_quantile=0.5).decide(CTX).warm[
        "shell"].copies == 1
    assert _decider(cold, {"shell": 0.9}, g, max_copies_per_tool=1).decide(CTX).warm[
        "shell"].copies == 1
    with pytest.raises(ValueError):
        DecideConfig.load(start_rule="later")


def test_merge_by_server_and_caps(cold: ColdStarts) -> None:
    warm = {"shell": WarmDecision(1, 3000.0, 0.7, 10_000.0),
            "git": WarmDecision(3, 1000.0, 0.6, 5_000.0),
            "other": WarmDecision(2, 0.0, 0.9, 30_000.0)}
    per = merge_by_server(warm, cold)
    assert per["shell"] == WarmDecision(3, 1000.0, 0.7, 10_000.0)
    cfg = DecideConfig.load(max_total_warm_pods=4)
    grant = apply_caps(per, {"shell": 1}, cfg, cold)
    # room = 4 - 1 = 3; generic (0.9 x 4,000) before shell (0.7 x 2,000)
    assert grant == {"generic": 2, "shell": 1}
    assert apply_caps(per, {}, cfg, cold, new_pod_budget=1) == {"generic": 1}


def test_last_batch_tool() -> None:
    evs = [Event("a", 0.0, 0), Event("z", 5.0, 1), Event("b", 5.0, 1)]
    assert last_batch_tool(Context.of(evs)) == "z"


# --------------------------------------------------------------------------- simulator
def _episode(batches: list[tuple[float, list[str]]], svc: float = 100.0) -> Episode:
    events, spans = [], []
    for i, (t, tools) in enumerate(batches):
        lo = len(events)
        events += [Event(x, t, i, svc) for x in sorted(tools)]
        spans.append((lo, len(events)))
    return Episode("s", "test", "g", True, tuple(events), spans, [None] * (len(batches) + 1))


def _env(cold: ColdStarts) -> Env:
    return Env(cold, "cached", load_config(), load_pricing(), DecideConfig.load())


def _run(spec: Spec, ep: Episode, cold: ColdStarts) -> Stats:
    st = Stats()
    simulate(ep, spec, _env(cold), ["read", "shell"], st)
    return st


EP = _episode([(0.0, ["read_file"]), (10_000.0, ["shell"]), (10_500.0, ["read_file"])])


def test_sim_baselines(cold: ColdStarts) -> None:
    s0 = _run(Spec("scale_to_zero", 0.0), EP, cold)
    assert (s0.calls, s0.warm, s0.hide_calls) == (3, 0, 1)  # only the shell call is hideable
    assert s0.added == [2000.0, 2000.0, 2000.0]
    aw = _run(Spec("always_warm", 30_000.0, always_warm=True), EP, cold)
    assert aw.warm == 3 and aw.unused_cpu_s == 0.0
    ka = _run(Spec("keep_alive_30", 30_000.0), EP, cold)
    assert ka.warm == 1  # read_file again within 30 s
    assert ka.fr3_max_lag_ms <= 1000.0  # removed at the next 1 s tick


def test_sim_oracle_warms_exactly_the_hideable_calls(cold: ColdStarts) -> None:
    st = _run(Spec("oracle", 0.0, oracle=True), EP, cold)
    assert st.warm == st.hide_calls == 1 and st.hide_cold == 0
    assert st.prewarm == 2 and st.prewarm_used == 2


def test_sim_predictive_policy_warms_and_holds(cold: ColdStarts) -> None:
    pol = _decider(cold, {"shell": 0.9}, _Graph(None, None))
    st = _run(Spec("ours", 30_000.0, 0.6, pol), EP, cold)
    assert st.warm == 2 and st.fr2_calls == 1 and st.fr2_ready == 1  # shell + kept read
    # the call arrives 500 ms after the decision: it waits for the starting pod (FR-9 hold)
    late = _episode([(0.0, ["read_file"]), (600.0, ["shell"])])
    st2 = _run(Spec("ours", 30_000.0, 0.6, pol), late, cold)
    assert st2.warm == 0 and st2.added[1] == pytest.approx(2000.0 - 500.0)


def test_sim_oracle_does_not_count_a_busy_pod(cold: ColdStarts) -> None:
    # batch 0's shell call runs 60 s; batch 1 overlaps it, batch 2 follows batch 1 by 10 s
    evs = [Event("shell", 0.0, 0, 60_000.0), Event("read_file", 1_000.0, 1, 100.0),
           Event("shell", 11_100.0, 2, 100.0)]
    ep = Episode("s", "test", "g", True, tuple(evs), [(0, 1), (1, 2), (2, 3)], [None] * 4)
    st = _run(Spec("oracle", 0.0, oracle=True), ep, cold)
    assert st.hide_calls == 1 and st.hide_cold == 0


def test_sim_keep_after_use_contract_rule(cold: ColdStarts) -> None:
    # read_file is called at 0 s and again at 10.5 s; the policy only ever predicts shell
    pol = _decider(cold, {"shell": 0.9}, _Graph(None, None), keep_after_use_s=2)
    short = _run(Spec("ours", 2_000.0, 0.6, pol, after_use="contract"), EP, cold)
    assert short.warm == 1  # shell prewarmed; read_file's pod went 2 s after its use
    pol30 = _decider(cold, {"read_file": 0.9}, _Graph(None, None), keep_after_use_s=2)
    kept = _run(Spec("ours", 2_000.0, 0.6, pol30, after_use="contract"), EP, cold)
    # read_file is named by every decision (ttl 30 s), so its pod is kept 30 s after use
    assert kept.warm == 1 and kept.added[2] == 0.0
