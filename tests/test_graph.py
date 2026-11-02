"""TransitionGraph (predictors/graph.py): features, back-off, poisoning guards, export."""

from __future__ import annotations

import json
import math
import random
from pathlib import Path

import pytest

from predictors.data import END, Context, Event, episodes, to_episode
from predictors.graph import (
    TransitionGraph,
    graph_features,
    rebuild,
    sanitize,
    top1_accuracy,
)
from traces.load import SAMPLES, load_sessions
from traces.schema import Batch, Call, Session


def _s(sid: str, batches: list[list[str]], gap: float = 1000.0, svc: float = 100.0,
       group: str | None = None) -> Session:
    bs, t = [], 0.0
    for i, tools in enumerate(batches):
        t += gap
        bs.append(Batch(i, t, None, [Call(x, svc, False, x) for x in tools]))
        t += svc
    return Session(sid, "toy", group or sid, True, bs)


def _ctx(*tools: str, node: str | None = None) -> Context:
    return Context.of([Event(t) for t in tools], node)


@pytest.fixture(scope="module")
def toy() -> TransitionGraph:
    sess = [_s(f"a{i}", [["read"], ["edit"], ["test"]]) for i in range(6)]
    sess += [_s(f"b{i}", [["read"], ["read", "read", "grep"], ["edit"]]) for i in range(4)]
    return TransitionGraph(min_support=3).fit(episodes(sess))


def test_one_step_and_back_off(toy: TransitionGraph) -> None:
    p = toy.one_step(_ctx("read", "edit"))
    assert sum(p.values()) == pytest.approx(1.0)
    assert set(p) <= {"test", END}
    first = toy.one_step(_ctx())
    assert first == {"read": 1.0}
    unseen = toy.one_step(_ctx("zz", "yy", "xx"))  # backs off to order 0, never raises
    assert unseen and sum(unseen.values()) == pytest.approx(1.0)
    assert toy.p_end(_ctx("read", "edit", "test")) == pytest.approx(1.0)


def test_k_step_is_within_k(toy: TransitionGraph) -> None:
    w3 = toy.k_step(_ctx(), 3)
    assert w3["read"] == pytest.approx(1.0)
    assert w3["edit"] == pytest.approx(1.0)
    assert w3["test"] == pytest.approx(0.6)
    assert all(0 <= v <= 1 for v in w3.values())


def test_batch_next_and_copies(toy: TransitionGraph) -> None:
    sets = dict(toy.batch_next(_ctx("read")))
    assert sets[frozenset({"edit"})] == pytest.approx(0.6)
    assert sets[frozenset({"grep", "read"})] == pytest.approx(0.4)
    c = toy.copies("read", _ctx("read"))
    assert c == {2: 1.0}
    assert toy.copies("never_seen") == {1: 1.0}


def test_fleet_demand_sums_sessions(toy: TransitionGraph) -> None:
    one = toy.expected_calls(_ctx("read"))
    assert one["read"] == pytest.approx(0.8)  # 4 of 10 sessions call read twice
    fleet = toy.fleet_demand([_ctx("read")] * 5 + [_ctx()] * 2)
    assert fleet["read"] == pytest.approx(5 * 0.8 + 2 * 1.0)


def test_timing_quantiles_and_fallback(toy: TransitionGraph) -> None:
    t = toy.timing("read", "edit")
    assert t.level == "pair" and t.p50 == pytest.approx(1000.0)
    assert toy.timing("nope", "edit").level == "next"
    g = toy.timing("nope", "nothing")
    assert g.level == "global" and g.n > 0
    empty = TransitionGraph().fit(episodes([Session("x", "toy", "x", False, [
        Batch(0, None, None, [Call("a")]), Batch(1, None, None, [Call("b")])])]))
    assert empty.timing("a", "b").level == "none"


def test_timings_are_winsorised() -> None:
    sess = [_s(f"s{i}", [["a"], ["b"]], gap=1000.0 + i) for i in range(200)]
    sess.append(_s("huge", [["a"], ["b"]], gap=1e12))
    g = TransitionGraph().fit(episodes(sess))
    assert g.timing("a", "b").p90 < 2000.0
    assert g.winsor[1] < 1e12


def test_per_session_cap_and_min_support() -> None:
    loop = [_s("loop", [["a"], ["b"]] * 500)]
    honest = [_s(f"h{i}", [["a"], ["c"]]) for i in range(4)]
    g = TransitionGraph(per_session_cap=3, min_support=3).fit(episodes(loop + honest))
    p = g.one_step(_ctx("a"))
    assert p["c"] > p.get("b", 0.0)  # 500 loops in one session count as 3
    rare = TransitionGraph(min_support=3).fit(episodes(
        [_s("r1", [["a"], ["z"]]), _s("r2", [["a"], ["z"]])] + honest))
    assert "z" not in rare.one_step(_ctx("a"))


def test_sanitize_drops_long_and_broken_sessions() -> None:
    eps = episodes([_s("ok", [["a"], ["b"]]), _s("long", [["a"]] * 2001)])
    bad = to_episode(_s("nan", [["a"], ["b"]]))
    bad.events = (bad.events[0], Event("b", float("nan"), 1, None, False))
    bad.events = tuple(e if e.t_offset_ms is None or math.isfinite(e.t_offset_ms)
                       else Event(e.tool, None, e.batch_id) for e in bad.events)
    kept, stats = sanitize([*eps, bad])
    assert [e.session_id for e in kept] == ["ok"]
    assert stats["dropped_too_many_events"] == 1 and stats["dropped_non_finite_time"] == 1


def test_node_table_used_when_node_known() -> None:
    from traces.generate_synthetic import sessions as synth

    eps = episodes(list(synth("branching", 300, seed=0, root=SAMPLES)))
    g = TransitionGraph().fit(eps)
    p = g.one_step(_ctx("order_lookup", node="lookup_order"))
    assert set(p) == {"policy_check", "refund_issue", END}
    assert p["policy_check"] == pytest.approx(0.7, abs=0.08)
    assert g.one_step(_ctx("order_lookup", node="unknown_node")) == g.predict(
        _ctx("order_lookup"), 1)


def test_export_roundtrip_and_determinism(tmp_path: Path) -> None:
    train = episodes(load_sessions("tracelab", "train", root=SAMPLES))
    a, b = TransitionGraph().fit(train), TransitionGraph().fit(list(reversed(train)))
    a.save(tmp_path / "a.json", "v1")
    b.save(tmp_path / "b.json", "v1")
    assert (tmp_path / "a.json").read_bytes() == (tmp_path / "b.json").read_bytes()
    d = json.loads((tmp_path / "a.json").read_text())
    for key in ("counts", "timing", "batch_stats", "vocabulary", "version", "train_hash",
                "schema_version"):
        assert key in d
    c = TransitionGraph.load(tmp_path / "a.json")
    test = episodes(load_sessions("tracelab", "test", root=SAMPLES))
    for ep in test[:10]:
        for i in range(ep.n_batches + 1):
            ctx = ep.context(i)
            assert graph_features(a, ctx) == graph_features(c, ctx)
    d["schema_version"] = 99
    with pytest.raises(ValueError):
        TransitionGraph.from_dict(d)


def test_junk_context_never_raises(toy: TransitionGraph) -> None:
    rng = random.Random(0)
    for _ in range(300):
        tools = [rng.choice(["read", "edit", "zz", "<end>", "<start>", ""])
                 for _ in range(rng.randrange(0, 8))]
        ctx = _ctx(*tools, node=rng.choice([None, "x"]))
        graph_features(toy, ctx)
        for k in (1, 2, 3, 4, 5, 9):
            toy.k_step(ctx, k)
            toy.predict(ctx, k)


def _junk(n: int) -> list:
    return episodes([_s(f"junk{i}", [["user_lookup"], ["order_cancel"]] * 50,
                        gap=1e12 if i % 2 else 0.0) for i in range(n)])


def test_per_session_cap_alone_does_not_stop_volume_poisoning() -> None:
    """Documents why rebuild() exists: many capped junk sessions still move top-1."""
    train = episodes(load_sessions("tau_bench", "train", root=SAMPLES))
    test = episodes(load_sessions("tau_bench", "test", root=SAMPLES))
    clean = top1_accuracy(TransitionGraph().fit(train), test)
    naive = top1_accuracy(TransitionGraph().fit(train + _junk(500)), test)
    assert naive < clean - 0.01


def test_rebuild_rejects_junk_and_keeps_top1() -> None:
    train = episodes(load_sessions("tau_bench", "train", root=SAMPLES))
    val = episodes(load_sessions("tau_bench", "val", root=SAMPLES))
    test = episodes(load_sessions("tau_bench", "test", root=SAMPLES))
    clean = top1_accuracy(TransitionGraph().fit(train), test)
    g, rep = rebuild(train, _junk(2000), val)
    assert rep.logged_weight <= 0.5 * len(train) + 1e-9
    assert abs(top1_accuracy(g, test) - clean) <= 0.01, rep
    assert g.winsor[1] <= 3_600_000.0  # absurd timings are clipped, not learned


def test_rebuild_accepts_clean_logged_traces() -> None:
    train = episodes(load_sessions("tau_bench", "train", root=SAMPLES))
    val = episodes(load_sessions("tau_bench", "val", root=SAMPLES))
    test = episodes(load_sessions("tau_bench", "test", root=SAMPLES))
    g, rep = rebuild(train, val, test)
    assert rep.accepted and rep.logged_kept == len(val)
    assert g.sanitize_stats["logged_sessions_kept"] == len(val)


def test_gap_hard_clip() -> None:
    sess = [_s(f"s{i}", [["a"], ["b"]], gap=1e12) for i in range(10)]
    g = TransitionGraph().fit(episodes(sess))
    assert g.timing("a", "b").p50 == pytest.approx(3_600_000.0)


def test_rebuild_gate_rejects_when_weighting_is_off() -> None:
    train = episodes(load_sessions("tau_bench", "train", root=SAMPLES))
    val = episodes(load_sessions("tau_bench", "val", root=SAMPLES))
    base = TransitionGraph().fit(train)
    g, rep = rebuild(train, _junk(2000), val, max_new_share=1e9)
    assert not rep.accepted and rep.reason.startswith("rejected")
    assert g.to_dict()["counts"] == base.to_dict()["counts"]


def test_cap_comes_from_config(tmp_path: Path) -> None:
    from predictors.graph import CONFIG

    assert TransitionGraph.from_config().per_session_cap == 3
    cfg = CONFIG.read_text().replace("per_session_cap: 3", "per_session_cap: null")
    (tmp_path / "g.yaml").write_text(cfg)
    g = TransitionGraph.from_config(tmp_path / "g.yaml")
    assert g.per_session_cap is None
    loop = episodes([_s("loop", [["a"], ["b"]] * 50)])
    assert g.fit(loop).tables["h1"]["a"]["b"] == 50.0
    assert TransitionGraph.from_config().fit(loop).tables["h1"]["a"]["b"] == 3.0
