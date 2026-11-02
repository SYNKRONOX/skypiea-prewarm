"""Evaluation harness (predictors/eval.py) and baseline ladder (predictors/baselines.py)."""

from __future__ import annotations

import csv
import math
from pathlib import Path

import numpy as np
import pytest

from predictors import eval as ev
from predictors.baselines import GraphBlend, Interpolated, Majority, Markov, ladder
from predictors.data import END, Context, Event, episodes, targets_ahead, to_episode
from traces.load import SAMPLES, load_sessions
from traces.schema import Batch, Call, Session


def _session(batches: list[list[str]], group: str = "g", node: list[str] | None = None,
             err: set[int] | None = None) -> Session:
    bs = [Batch(i, float(i * 1000), node[i] if node else None,
                [Call(t, 5.0, bool(err and i in err), t) for t in tools])
          for i, tools in enumerate(batches)]
    return Session(f"s-{group}-{len(batches)}", "toy", group, True, bs)


def test_episode_decision_points_and_targets() -> None:
    ep = to_episode(_session([["a"], ["c", "b"], ["a"]], err={1}))
    assert [e.tool for e in ep.events] == ["a", "b", "c", "a"]
    assert ep.batches == [(0, 1), (1, 3), (3, 4)]
    assert ep.context(1).tools(5) == ("a",)
    assert ep.context(2).tools(5) == ("a", "b", "c")
    assert ep.errors_before == [False, False, True]
    assert targets_ahead(ep, 0, 1) == ["a"]
    assert targets_ahead(ep, 0, 3) == ["a"]
    assert targets_ahead(ep, 1, 3) == []


def test_node_only_batches_set_the_node() -> None:
    s = Session("x", "synthetic_branching", "x", True, [
        Batch(0, 0.0, "classify_intent", []),
        Batch(1, 1.0, "lookup_order", [Call("order_lookup")]),
        Batch(2, 2.0, "notify_user", [])])
    ep = to_episode(s)
    assert ep.context(0).node == "classify_intent"
    assert ep.context(1).node == "notify_user"  # end point


def test_markov_and_majority_on_toy_data() -> None:
    train = episodes([_session([["a"], ["b"], ["a"], ["b"]], f"g{i}") for i in range(5)])
    m1 = Markov(1).fit(train, [])
    p = m1.predict(Context.of([Event("a")]), 1)
    assert p == {"b": 1.0}
    p_end = m1.predict(Context.of([Event("a"), Event("b"), Event("a"), Event("b")]), 1)
    assert sum(p_end.values()) == pytest.approx(1.0) and p_end.get(END, 0) > 0
    assert Majority().fit(train, []).predict(Context.of([]), 1) in ({"a": 1.0}, {"b": 1.0})


def test_graph_blend_uses_workflow_edges_on_synthetic() -> None:
    from traces.generate_synthetic import sessions as synth

    eps = episodes(list(synth("branching", 300, seed=0, root=SAMPLES)))
    gb = GraphBlend().fit(eps[:200], eps[200:])
    assert gb.edge_label == "workflow-defined edges"
    allowed = gb.edges.allowed(Context.of([Event("order_lookup")], "lookup_order"), 1)
    assert allowed == {"policy_check", "refund_issue", END}
    real = GraphBlend().fit(episodes(load_sessions("tau_bench", "train", root=SAMPLES)), [])
    assert real.edge_label == "learned edges, not workflow-defined"


@pytest.mark.parametrize("source", ["tau_bench", "tracelab"])
def test_all_models_return_valid_distributions(source: str) -> None:
    train = episodes(load_sessions(source, "train", root=SAMPLES))
    val = episodes(load_sessions(source, "val", root=SAMPLES))
    test = episodes(load_sessions(source, "test", root=SAMPLES))
    for m in ladder():
        m.fit(train, val)
        for ep in test[:20]:
            for i in range(ep.n_batches + 1):
                for k in ev.KS:
                    p = m.predict(ep.context(i), k)
                    assert p and all(0.0 <= v <= 1.0 for v in p.values())
                    assert sum(p.values()) <= 1.0 + 1e-9, m.name


def test_interpolated_weights_sum_to_one() -> None:
    train = episodes(load_sessions("tau_bench", "train", root=SAMPLES))
    val = episodes(load_sessions("tau_bench", "val", root=SAMPLES))
    m = Interpolated().fit(train, val)
    assert sum(m.weights) == pytest.approx(1.0)


def test_ece_and_reliability() -> None:
    conf = np.array([0.95, 0.95, 0.15, 0.15])
    cor = np.array([1.0, 1.0, 0.0, 0.0])
    assert ev.ece(conf, cor) == pytest.approx(0.1)
    rows = ev.reliability(conf, cor)
    assert rows[9]["n"] == 2 and rows[9]["accuracy"] == 1.0


def test_group_bootstrap_ci_contains_point_and_shrinks() -> None:
    rng = np.random.default_rng(0)
    rec = ev.Records()
    for g in range(50):
        for _ in range(20):
            rec.add("m", float(rng.random() < 0.6), g)
    ci = ev.group_bootstrap(rec, 50)["m"]
    point = np.mean(rec.num["m"])
    assert ci[0] < point < ci[1] and ci[1] - ci[0] < 0.1


@pytest.mark.parametrize("split", ["gss:0", "gkf:3"])
def test_generated_splits_keep_groups_apart(split: str) -> None:
    tr, va, te = ev.split_groups("tau_bench", split, SAMPLES)
    gs = [{e.group for e in part} for part in (tr, va, te)]
    assert gs[0] and gs[1] and gs[2]
    assert not (gs[0] & gs[1] or gs[0] & gs[2] or gs[1] & gs[2])


def test_evaluate_writes_csv(tmp_path: Path) -> None:
    out = tmp_path / "p1.csv"
    r = ev.evaluate(Markov(1), "tau_bench", "fixed", root=SAMPLES, n_boot=50, csv_path=out)
    assert 0 <= r["top1_k1"] <= r["top3_k1"] <= 1
    assert r["p99_ms"] < 50
    rows = list(csv.DictReader(out.open()))
    assert {"top1_k1", "top3_k5", "within3_recall", "ece"} <= {x["metric"] for x in rows}
    assert all(x["git_commit"] for x in rows)
    lo, hi = next((float(x["ci_lo"]), float(x["ci_hi"])) for x in rows
                  if x["metric"] == "top1_k1")
    assert lo <= r["top1_k1"] <= hi and not math.isnan(lo)


def test_horizon_beyond_every_train_session_predicts_end() -> None:
    train = episodes([_session([["a"], ["b"]], f"g{i}") for i in range(5)])
    ctx = Context.of([Event("a")])
    for m in ladder():
        m.fit(train, train)
        p = m.predict(ctx, 5)
        assert p and sum(p.values()) <= 1 + 1e-9, m.name
    assert Markov(1).fit(train, []).predict(Context.of([]), 5) == {END: 1.0}
