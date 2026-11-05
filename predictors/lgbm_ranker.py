"""LightGBM re-ranker over Markov / n-gram features (Phase 4).

    r = LGBMRanker.from_config().fit(train, val)
    r.ranked(ctx)        # FR-1: distribution over next tools incl. "<end>", sums to <= 1
    r.batch_probs(ctx)   # P(tool in the next batch), may sum above 1
    r.save(folder); LGBMRanker.load(folder)

Candidates per decision point: the interpolated n-gram's top-20, unioned with the top-5 of
the graph-prior blend, plus ``<end>``. Both base models are computed from the transition
graph's guarded count tables (predictors/graph_models.py), so counts.json is the only
count artifact. Each (decision point, candidate) row gets the features
in ``FEATURES``; the label is 1 if the candidate is in the next batch (``<end>``: the session
ends). LightGBM (binary objective, fixed seed, ``num_threads`` from config) is trained on
rows from TRAIN whose base-model features are computed out-of-fold (group k-fold), so the
ranker never sees in-sample n-gram scores; early stopping uses VAL rows (disjoint groups),
whose features come from base models fitted on all of train. An isotonic regression is always
fitted on VAL and stored as JSON breakpoints; config ``calibration`` decides whether it is
applied (``isotonic``) or the raw LightGBM scores are used (``none``, the default). When
applied (``np.interp``), ties between its flat steps are broken by the raw score, so
calibration never reorders candidates.

Two outputs from the same scores c_j:
  (a) ranked: q_j = c_j / sum(c), over candidates incl. ``<end>`` (sums to <= 1);
  (b) batch_probs: c_j itself (marginal, may sum above 1), for provisioning decisions.
Horizons k > 1 are not modelled by the ranker; ``predict(ctx, k)`` uses the n-gram there.

No Python object serialisation anywhere: the booster is LightGBM's text format and
everything else is JSON.
"""

from __future__ import annotations

import json
import math
import random
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import yaml

from predictors.base import NextToolModel
from predictors.data import END, Context, Episode
from predictors.graph import TransitionGraph
from predictors.graph_models import GraphNgram, GraphPriorBlend
from predictors.workflow import START

CONFIG = Path(__file__).resolve().parent.parent / "configs" / "model" / "ranker.yaml"
SCHEMA_VERSION = 1
CALIBRATIONS = ("none", "isotonic")
MISSING = -1.0  # numeric features; categorical codes: unseen = n, "no value" = n + 1
CAT_FEATURES = ["cand", "last1", "last2", "last3", "last4", "last5", "node"]
FEATURES = [
    "cand", "is_end", "ngram", "graph_blend", "one_step", "k_step_2", "k_step_3", "k_step_5",
    "expected_calls", "gap_p10", "gap_p50", "gap_p90", "gap_level", "p_end",
    "last1", "last2", "last3", "last4", "last5", "last_batch_size", "last_error",
    "last_service_ms", "step", "t_since_start_ms", "cand_freq", "cand_recency",
    "cand_is_last", "ngram_rank", "node",
]
TIE_EPS = 1e-6  # see LGBMRanker._calibrate
GAP_LEVEL = {"pair": 3.0, "next": 2.0, "global": 1.0, "none": 0.0}


def _fsum_normalise(c: dict[str, float]) -> dict[str, float]:
    """Divide by the sum; guarantee the result sums to at most 1 in floating point."""
    total = math.fsum(c.values())
    if total <= 0:
        return {}
    q = {t: v / total for t, v in c.items()}
    s = math.fsum(q.values())
    if s > 1.0:
        q = {t: v / s * (1.0 - 1e-12) for t, v in q.items()}
    return q


class Calibrator:
    """Isotonic map from raw score to probability, as JSON breakpoints."""

    def __init__(self, x: Sequence[float] = (0.0, 1.0), y: Sequence[float] = (0.0, 1.0)) -> None:
        self.x = np.asarray(x, dtype=float)
        self.y = np.clip(np.asarray(y, dtype=float), 0.0, 1.0)

    @classmethod
    def fit(cls, scores: np.ndarray, labels: np.ndarray) -> Calibrator:
        from sklearn.isotonic import IsotonicRegression  # training only; not in the image

        iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip").fit(scores, labels)
        return cls(iso.X_thresholds_.tolist(), iso.y_thresholds_.tolist())

    def __call__(self, s: np.ndarray) -> np.ndarray:
        if len(self.x) == 1:
            return np.full_like(np.asarray(s, dtype=float), self.y[0])
        return np.interp(s, self.x, self.y)

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": SCHEMA_VERSION, "kind": "isotonic",
                "x": [float(v) for v in self.x], "y": [float(v) for v in self.y]}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Calibrator:
        if d.get("kind") != "isotonic" or d.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("unsupported calibrator")
        x, y = d["x"], d["y"]
        if len(x) != len(y) or not x or any(b < a for a, b in zip(x, x[1:], strict=False)):
            raise ValueError("calibrator breakpoints must be non-empty and sorted")
        if not all(math.isfinite(v) for v in [*x, *y]):
            raise ValueError("calibrator breakpoints must be finite")
        return cls(x, y)


class _Base:
    """The base models whose outputs become features, all computed from ONE transition
    graph (so counts.json plus two tuned parameters define them)."""

    def __init__(self, graph: TransitionGraph, ngram: GraphNgram,
                 blend: GraphPriorBlend) -> None:
        self.graph, self.ngram, self.blend = graph, ngram, blend

    @classmethod
    def fit(cls, train: Sequence[Episode], val: Sequence[Episode],
            graph_overrides: dict[str, Any] | None = None) -> _Base:
        g = TransitionGraph.from_config(**(graph_overrides or {})).fit(train)
        return cls(g, GraphNgram(g).fit(train, val), GraphPriorBlend(g).fit(train, val))

    @classmethod
    def from_params(cls, graph: TransitionGraph, p: dict[str, Any]) -> _Base:
        return cls(graph, GraphNgram(graph, p["ngram_weights"]),
                   GraphPriorBlend(graph, p["blend_scenario"], p["blend_alpha"]))

    def params(self) -> dict[str, Any]:
        return {"ngram_weights": list(self.ngram.weights), "blend_alpha": self.blend.alpha,
                "blend_scenario": self.blend.scenario}


class LGBMRanker(NextToolModel):
    name = "lgbm_ranker"

    def __init__(self, cfg: dict[str, Any] | None = None,
                 calibration: str | None = None) -> None:
        self.cfg = cfg if cfg is not None else yaml.safe_load(CONFIG.read_text())
        self.calibration = calibration or str(self.cfg.get("calibration", "none"))
        if self.calibration not in CALIBRATIONS:
            raise ValueError(f"calibration must be one of {CALIBRATIONS}")
        self.base: _Base | None = None
        self.booster: lgb.Booster | None = None
        self.calibrator = Calibrator()
        self.vocab: dict[str, int] = {}
        self.nodes: dict[str, int] = {}
        self.best_iteration = 0
        self.val_ece: dict[str, float] = {}

    @classmethod
    def from_config(cls, path: Path = CONFIG) -> LGBMRanker:
        return cls(yaml.safe_load(path.read_text(encoding="utf-8")))

    # ------------------------------------------------------------------ features
    def _code(self, tool: str) -> float:
        return float(self.vocab.get(tool, len(self.vocab)))  # unseen -> one shared code

    def candidates(self, base: _Base, ctx: Context) -> tuple[list[str], dict[str, float],
                                                              dict[str, float]]:
        c = self.cfg["candidates"]
        ng = base.ngram.predict(ctx, 1)
        bl = base.blend.predict(ctx, 1)
        top = sorted(ng, key=lambda t: (-ng[t], t))[: c["ngram_top"]]
        top += [t for t in sorted(bl, key=lambda t: (-bl[t], t))[: c["graph_top"]]
                if t not in top]
        if END not in top:
            top.append(END)
        return top, ng, bl

    def rows(self, base: _Base, ctx: Context) -> tuple[list[str], np.ndarray]:
        """Candidates and their feature matrix (len(cands) x len(FEATURES))."""
        cands, ng, bl = self.candidates(base, ctx)
        g = base.graph
        one = g.one_step(ctx)
        ks = {k: g.k_step(ctx, k) for k in (2, 3, 5)}
        calls = g.expected_calls(ctx)
        p_end = g.p_end(ctx)
        prefix = ctx.prefix()
        last = ctx.tools(5)
        no_tool = float(len(self.vocab) + 1)
        lastn = [self._code(t) for t in reversed(last)] + [no_tool] * (5 - len(last))
        prev = last[-1] if last else START
        if prefix:
            lb = prefix[-1].batch_id
            batch = [e for e in prefix[-8:] if e.batch_id == lb]
            last_bs, last_err = float(len(batch)), float(any(e.error for e in batch))
            svc = prefix[-1].service_ms
            last_svc = MISSING if svc is None else float(svc)
            t = prefix[-1].t_offset_ms
            t_since = MISSING if t is None else float(t)
        else:
            last_bs, last_err, last_svc, t_since = 0.0, 0.0, MISSING, MISSING
        step = float(ctx.length)
        seen: dict[str, tuple[int, int]] = {}
        for pos, e in enumerate(prefix):
            n, _ = seen.get(e.tool, (0, 0))
            seen[e.tool] = (n + 1, pos)
        node = (float(len(self.nodes) + 1) if ctx.node is None
                else float(self.nodes.get(ctx.node, len(self.nodes))))
        ng_rank = {t: i for i, t in enumerate(sorted(ng, key=lambda t: (-ng[t], t)))}
        out = np.empty((len(cands), len(FEATURES)), dtype=np.float32)
        for r, cand in enumerate(cands):
            tm = g.timing(prev, cand) if cand != END else None
            n, pos = seen.get(cand, (0, -1))
            out[r] = [
                self._code(cand), float(cand == END), ng.get(cand, 0.0), bl.get(cand, 0.0),
                one.get(cand, 0.0), ks[2].get(cand, 0.0), ks[3].get(cand, 0.0),
                ks[5].get(cand, 0.0), calls.get(cand, 0.0),
                MISSING if tm is None or tm.p10 is None else tm.p10,
                MISSING if tm is None or tm.p50 is None else tm.p50,
                MISSING if tm is None or tm.p90 is None else tm.p90,
                GAP_LEVEL[tm.level] if tm is not None else MISSING, p_end,
                *lastn, last_bs, last_err, last_svc, step, t_since,
                n / step if step else 0.0, float(len(prefix) - pos) if pos >= 0 else MISSING,
                float(bool(last) and cand == last[-1]), float(ng_rank.get(cand, 99)), node,
            ]
        return cands, out

    def _dataset(self, base: _Base, points: Sequence[tuple[Context, set[str]]]
                 ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        xs, ys, gid = [], [], []
        for i, (ctx, true) in enumerate(points):
            cands, x = self.rows(base, ctx)
            xs.append(x)
            ys.extend(float(c in true) for c in cands)
            gid.extend([i] * len(cands))
        if not xs:
            return (np.empty((0, len(FEATURES)), np.float32), np.empty(0), np.empty(0))
        return np.vstack(xs), np.asarray(ys), np.asarray(gid)

    @staticmethod
    def _points(eps: Sequence[Episode], limit: int, seed: int
                ) -> list[tuple[Episode, int]]:
        pts = [(ep, i) for ep in eps for i in range(ep.n_batches + 1)]
        if len(pts) > limit:
            pts = random.Random(seed).sample(pts, limit)
        return pts

    @staticmethod
    def _truth(ep: Episode, i: int) -> set[str]:
        return set(ep.batch_tools(i)) if i < ep.n_batches else {END}

    # ------------------------------------------------------------------ fitting
    def fit(self, train: Sequence[Episode], val: Sequence[Episode]) -> LGBMRanker:
        cfg, seed = self.cfg, int(self.cfg["seed"])
        tools = sorted({e.tool for ep in train for e in ep.events} | {END})
        self.vocab = {t: i for i, t in enumerate(tools)}
        self.nodes = {n: i for i, n in enumerate(sorted(
            {n for ep in train for n in ep.nodes if n is not None}))}
        self.mean_batch_size = sum(len(ep.events) for ep in train) / max(
            sum(ep.n_batches for ep in train), 1)

        # Out-of-fold base features for the training rows: folds by group, or by session
        # when there are fewer groups than folds (only on tiny data such as the samples).
        n_folds = int(cfg["oof_folds"])
        groups = sorted({ep.group for ep in train})
        unit = (lambda ep: ep.group) if len(groups) >= n_folds else (lambda ep: ep.session_id)
        units = sorted({unit(ep) for ep in train})
        random.Random(seed).shuffle(units)
        n_folds = max(2, min(n_folds, len(units)))
        fold_unit = {u: i % n_folds for i, u in enumerate(units)}
        fold_of = {ep.session_id: fold_unit[unit(ep)] for ep in train}
        sampled = self._points(train, int(cfg["max_train_points"]), seed)
        xs, ys, gs = [], [], []
        for f in range(n_folds):
            fit_eps = [ep for ep in train if fold_of[ep.session_id] != f]
            pts = [(ep.context(i), self._truth(ep, i)) for ep, i in sampled
                   if fold_of[ep.session_id] == f]
            if not fit_eps or not pts:
                continue
            x, y, g = self._dataset(_Base.fit(fit_eps, val, cfg.get("graph")), pts)
            xs.append(x)
            ys.append(y)
            gs.append(g + (gs[-1].max() + 1 if gs else 0))
        x_tr, y_tr = np.vstack(xs), np.concatenate(ys)

        self.base = _Base.fit(train, val, cfg.get("graph"))
        vpts = [(ep.context(i), self._truth(ep, i))
                for ep, i in self._points(val, int(cfg["max_val_points"]), seed + 1)]
        x_va, y_va, _ = self._dataset(self.base, vpts)

        p = cfg["lgbm"]
        params = {
            "objective": p["objective"], "learning_rate": p["learning_rate"],
            "num_leaves": p["num_leaves"], "min_data_in_leaf": p["min_data_in_leaf"],
            "feature_fraction": p["feature_fraction"], "bagging_fraction": p["bagging_fraction"],
            "lambda_l2": p["lambda_l2"], "seed": seed, "deterministic": True,
            "force_row_wise": True, "num_threads": int(cfg["num_threads"]), "verbosity": -1,
            "max_cat_to_onehot": 32,
        }
        dtr = lgb.Dataset(x_tr, y_tr, feature_name=FEATURES, categorical_feature=CAT_FEATURES,
                          free_raw_data=False)
        callbacks = [lgb.log_evaluation(0)]
        valid = []
        if len(y_va) and 0 < y_va.sum() < len(y_va):
            valid = [lgb.Dataset(x_va, y_va, reference=dtr)]
            callbacks.append(lgb.early_stopping(int(p["early_stopping_rounds"]), verbose=False))
        self.booster = lgb.train(params, dtr, num_boost_round=int(p["max_rounds"]),
                                 valid_sets=valid, callbacks=callbacks)
        self.best_iteration = int(self.booster.best_iteration or self.booster.current_iteration())
        if len(y_va):
            raw = self._raw(x_va)
            self.calibrator = Calibrator.fit(raw, y_va)
            self.val_ece = {"raw": _row_ece(raw, y_va),
                            "calibrated": _row_ece(self.calibrator(raw), y_va)}
        return self

    # ------------------------------------------------------------------ inference
    def _raw(self, x: np.ndarray) -> np.ndarray:
        assert self.booster is not None
        return np.asarray(self.booster.predict(x, num_iteration=self.best_iteration),
                          dtype=float)

    def _calibrate(self, raw: np.ndarray) -> np.ndarray:
        """Calibrated scores, ties broken by the raw score.

        Isotonic regression is a step function, so distinct raw scores can share one
        calibrated value; without the tie-break their order would fall back to tool names.
        The convex mix keeps values in [0, 1] and moves them by at most ``TIE_EPS``.
        """
        if self.calibration == "none":
            return raw
        return (1.0 - TIE_EPS) * self.calibrator(raw) + TIE_EPS * raw

    def scores(self, ctx: Context) -> dict[str, float]:
        """P(candidate in the next batch): raw, or isotonic if ``calibration="isotonic"``."""
        assert self.base is not None
        cands, x = self.rows(self.base, ctx)
        raw = self._raw(x)
        s = self._calibrate(raw)
        return {c: float(min(1.0, max(0.0, v))) for c, v in zip(cands, s, strict=True)}

    def ranked(self, ctx: Context) -> dict[str, float]:
        """FR-1 distribution over next tools incl. END; sums to at most 1."""
        return _fsum_normalise(self.scores(ctx))

    def predict(self, ctx: Context, k: int = 1) -> dict[str, float]:
        if k == 1:
            return self.ranked(ctx)
        assert self.base is not None
        return self.base.ngram.predict(ctx, k)

    def predict_many(self, ctxs: Sequence[Context], k: int = 1) -> list[dict[str, float]]:
        if k != 1 or not ctxs:
            return [self.predict(c, k) for c in ctxs]
        assert self.base is not None
        built = [self.rows(self.base, c) for c in ctxs]
        raw = self._raw(np.vstack([x for _, x in built]))
        s = self._calibrate(raw)
        out, i = [], 0
        for cands, x in built:
            vals = s[i:i + len(x)]
            i += len(x)
            out.append(_fsum_normalise({c: float(min(1.0, max(0.0, v)))
                                        for c, v in zip(cands, vals, strict=True)}))
        return out

    def batch_probs(self, ctx: Context) -> dict[str, float]:
        return {t: p for t, p in self.scores(ctx).items() if t != END}

    def batch_probs_many(self, ctxs: Sequence[Context], chunk: int = 2000
                         ) -> list[dict[str, float]]:
        """``batch_probs`` for many contexts, one booster call per chunk."""
        assert self.base is not None
        out: list[dict[str, float]] = []
        for lo in range(0, len(ctxs), chunk):
            built = [self.rows(self.base, c) for c in ctxs[lo:lo + chunk]]
            if not built:
                continue
            s = self._calibrate(self._raw(np.vstack([x for _, x in built])))
            i = 0
            for cands, x in built:
                vals = s[i:i + len(x)]
                i += len(x)
                out.append({c: float(min(1.0, max(0.0, v)))
                            for c, v in zip(cands, vals, strict=True) if c != END})
        return out

    def feature_importance(self, top: int = 15) -> list[tuple[str, float]]:
        assert self.booster is not None
        gain = self.booster.feature_importance("gain", iteration=self.best_iteration)
        return sorted(zip(FEATURES, (float(v) for v in gain), strict=True),
                      key=lambda kv: -kv[1])[:top]

    # ------------------------------------------------------------------ export
    def save(self, folder: Path) -> None:
        """ranker.lgb (LightGBM text), calibrator.json, ranker_meta.json, counts.json."""
        assert self.booster is not None and self.base is not None
        folder.mkdir(parents=True, exist_ok=True)
        self.booster.save_model(str(folder / "ranker.lgb"), num_iteration=self.best_iteration)
        (folder / "calibrator.json").write_text(
            json.dumps(self.calibrator.to_dict(), sort_keys=True) + "\n", encoding="utf-8")
        meta = {"schema_version": SCHEMA_VERSION, "features": FEATURES,
                "categorical": CAT_FEATURES, "vocab": self.vocab, "nodes": self.nodes,
                "best_iteration": self.best_iteration, "config": self.cfg,
                "mean_batch_size": self.mean_batch_size, "val_ece": self.val_ece,
                **self.base.params()}
        (folder / "ranker_meta.json").write_text(json.dumps(meta, sort_keys=True, indent=1)
                                                 + "\n", encoding="utf-8")
        self.base.graph.save(folder / "counts.json")

    FILES = ("ranker.lgb", "calibrator.json", "ranker_meta.json", "counts.json")

    @classmethod
    def load(cls, folder: Path) -> LGBMRanker:
        """Load from the files written by ``save`` (JSON and LightGBM text only)."""
        return cls.from_files({n: (folder / n).read_bytes() for n in cls.FILES})

    @classmethod
    def from_files(cls, files: Mapping[str, bytes]) -> LGBMRanker:
        """Load from the bytes of the files ``save`` writes (no disk access)."""
        meta = json.loads(files["ranker_meta.json"].decode("utf-8"))
        if meta.get("schema_version") != SCHEMA_VERSION or meta.get("features") != FEATURES:
            raise ValueError("ranker metadata does not match this code's features")
        r = cls(meta["config"])
        r.vocab = {str(k): int(v) for k, v in meta["vocab"].items()}
        r.nodes = {str(k): int(v) for k, v in meta["nodes"].items()}
        r.best_iteration = int(meta["best_iteration"])
        r.mean_batch_size = float(meta["mean_batch_size"])
        r.val_ece = dict(meta.get("val_ece", {}))
        r.booster = lgb.Booster(model_str=files["ranker.lgb"].decode("utf-8"))
        r.calibrator = Calibrator.from_dict(json.loads(files["calibrator.json"].decode("utf-8")))
        r.base = _Base.from_params(
            TransitionGraph.from_dict(json.loads(files["counts.json"].decode("utf-8"))), meta)
        return r


def _row_ece(p: np.ndarray, y: np.ndarray, bins: int = 10) -> float:
    """ECE of per-row in-next-batch probabilities."""
    b = np.minimum((np.clip(p, 0, 1) * bins).astype(int), bins - 1)
    cnt = np.bincount(b, minlength=bins)
    gap = np.abs(np.bincount(b, weights=p, minlength=bins) - np.bincount(b, weights=y,
                                                                        minlength=bins))
    return float(gap.sum() / max(cnt.sum(), 1))
