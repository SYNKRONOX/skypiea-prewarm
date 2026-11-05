"""LightGBM re-ranker (predictors/lgbm_ranker.py) and graph-derived base models, on samples."""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pytest

from predictors import eval as ev
from predictors.data import END, Context, Event, episodes
from predictors.graph import TransitionGraph
from predictors.graph_models import GraphNgram, GraphPriorBlend
from predictors.lgbm_ranker import FEATURES, Calibrator, LGBMRanker
from traces.load import SAMPLES, load_sessions


def _split(source: str = "tracelab") -> list:
    return [episodes(load_sessions(source, s, root=SAMPLES)) for s in ("train", "val", "test")]


@pytest.fixture(scope="module")
def fitted() -> tuple[LGBMRanker, list]:
    tr, va, te = _split()
    return LGBMRanker.from_config().fit(tr, va), te


def _contexts(test: list, n: int = 200) -> list[Context]:
    return [ep.context(i) for ep in test for i in range(ep.n_batches + 1)][:n]


def test_outputs_satisfy_fr1(fitted: tuple[LGBMRanker, list]) -> None:
    r, te = fitted
    for ctx in _contexts(te):
        q = r.ranked(ctx)
        assert END in q
        assert sum(q.values()) <= 1.0 + 1e-12
        assert all(0.0 <= v <= 1.0 for v in q.values())
        assert len([t for t in q if t != END]) >= 3
        b = r.batch_probs(ctx)
        assert END not in b and all(0.0 <= v <= 1.0 for v in b.values())
        assert r.predict(ctx, 3)  # k > 1 falls back to the n-gram


def test_predict_many_matches_predict(fitted: tuple[LGBMRanker, list]) -> None:
    r, te = fitted
    ctxs = _contexts(te, 60)
    assert r.predict_many(ctxs, 1) == [r.predict(c, 1) for c in ctxs]


def test_rows_and_candidates(fitted: tuple[LGBMRanker, list]) -> None:
    r, te = fitted
    assert r.base is not None
    ctx = _contexts(te)[5]
    cands, x = r.rows(r.base, ctx)
    assert x.shape == (len(cands), len(FEATURES))
    assert END in cands and len(set(cands)) == len(cands)
    ng = r.base.ngram.predict(ctx, 1)
    assert set(sorted(ng, key=lambda t: (-ng[t], t))[:20]) <= set(cands)
    assert np.isfinite(x).all()


def test_unseen_inputs_never_raise(fitted: tuple[LGBMRanker, list]) -> None:
    r, _ = fitted
    for ctx in (Context.of([]), Context.of([Event("never_seen_tool")] * 300),
                Context.of([Event("read_file", None, 0, None, True)] * 5, node="unknown")):
        q = r.ranked(ctx)
        assert q and sum(q.values()) <= 1.0 + 1e-12


def test_calibrator_json_roundtrip_and_validation() -> None:
    rng = np.random.default_rng(0)
    s = rng.random(2000)
    y = (rng.random(2000) < s**2).astype(float)
    c = Calibrator.fit(s, y)
    out = c(np.linspace(0, 1, 50))
    assert np.all(np.diff(out) >= -1e-12) and out.min() >= 0 and out.max() <= 1
    c2 = Calibrator.from_dict(json.loads(json.dumps(c.to_dict())))
    assert np.array_equal(c2(s), c(s))
    for bad in ({"kind": "isotonic", "schema_version": 1, "x": [1, 0], "y": [0, 1]},
                {"kind": "isotonic", "schema_version": 1, "x": [0, float("nan")], "y": [0, 1]},
                {"kind": "pickle", "schema_version": 1, "x": [0], "y": [0]}):
        with pytest.raises(ValueError):
            Calibrator.from_dict(bad)


def test_save_load_roundtrip_no_pickle(fitted: tuple[LGBMRanker, list], tmp_path: Path) -> None:
    r, te = fitted
    r.save(tmp_path)
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["calibrator.json", "counts.json", "ranker.lgb", "ranker_meta.json"]
    assert not any(p.suffix in {".pkl", ".pickle", ".joblib"} for p in tmp_path.iterdir())
    r2 = LGBMRanker.load(tmp_path)
    for ctx in _contexts(te, 100):
        assert r2.ranked(ctx) == r.ranked(ctx)


def test_training_is_deterministic(tmp_path: Path) -> None:
    tr, va, _ = _split("tau_bench")
    for name in ("a", "b"):
        LGBMRanker.from_config().fit(tr, va).save(tmp_path / name)
    for f in ("ranker.lgb", "calibrator.json", "counts.json", "ranker_meta.json"):
        assert (tmp_path / "a" / f).read_bytes() == (tmp_path / "b" / f).read_bytes(), f


def _isotonic(r: LGBMRanker) -> LGBMRanker:
    iso = LGBMRanker(r.cfg, calibration="isotonic")
    iso.__dict__.update({k: v for k, v in r.__dict__.items() if k != "calibration"})
    return iso


def test_calibration_modes_and_feature_importance(fitted: tuple[LGBMRanker, list]) -> None:
    r, te = fitted
    assert r.calibration == "none"  # shipped default: raw scores
    iso = _isotonic(r)
    ctx = _contexts(te)[3]
    assert iso.scores(ctx) != r.scores(ctx)
    with pytest.raises(ValueError):
        LGBMRanker(r.cfg, calibration="platt")
    imp = r.feature_importance(15)
    assert len(imp) == 15 and {f for f, _ in imp} <= set(FEATURES)


def test_calibration_never_reorders_candidates(fitted: tuple[LGBMRanker, list]) -> None:
    r, te = fitted
    r, raw = _isotonic(r), r
    for ctx in _contexts(te):
        a, b = r.scores(ctx), raw.scores(ctx)
        order = sorted(b, key=lambda t: (-b[t], t))
        assert sorted(a, key=lambda t: (-a[t], t)) == order
        assert ev.top_n(r.predict(ctx), 3) == ev.top_n(raw.predict(ctx), 3)


def test_latency_on_sample(fitted: tuple[LGBMRanker, list]) -> None:
    r, te = fitted
    ts = []
    for ctx in _contexts(te, 300):
        t0 = time.perf_counter()
        ev.top_n(r.ranked(ctx), 3)
        ts.append((time.perf_counter() - t0) * 1000)
    assert np.percentile(ts, 99) < 40.0  # internal budget (FR-1 limit is 50 ms)


def test_graph_models_are_valid_and_use_workflow_edges() -> None:
    from traces.generate_synthetic import sessions as synth

    tr, va, te = _split("tau_bench")
    g = TransitionGraph().fit(tr)
    for m in (GraphNgram(g).fit(tr, va), GraphPriorBlend(g).fit(tr, va)):
        for ep in te[:10]:
            for i in range(ep.n_batches + 1):
                p = m.predict(ep.context(i), 1)
                assert p and sum(p.values()) <= 1 + 1e-9
    eps = episodes(list(synth("branching", 200, seed=0, root=SAMPLES)))
    gb = GraphPriorBlend(TransitionGraph().fit(eps[:150])).fit(eps[:150], eps[150:])
    assert gb.edge_label == "workflow-defined edges"
    ctx = Context.of([Event("order_lookup")], "lookup_order")
    assert gb.allowed(ctx) == {"policy_check", "refund_issue", END}


def test_cross_source_evaluate(tmp_path: Path) -> None:
    out = tmp_path / "p1.csv"
    r = ev.evaluate(LGBMRanker.from_config(), "tracelab", "fixed", "coding_common", root=SAMPLES,
                    n_boot=20, csv_path=out, test_source="copilot_2026")
    assert r["source"] == "tracelab->copilot_2026"
    assert 0.0 <= r["top1_k1"] <= r["top3_k1"] <= 1.0
