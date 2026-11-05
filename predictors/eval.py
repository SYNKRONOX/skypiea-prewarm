"""Evaluation harness for next-tool models (Phase 1).

    python -m predictors.eval --source tracelab --models all --splits fixed,gss,gkf
    python -m predictors.eval --source copilot_2026 --vocab coding_common --models markov1

``evaluate(model, source, split, vocab)`` fits a fresh copy of ``model`` on the split's
train part (tuning only on its val part), scores the test part and appends one row per
metric to results/p1_accuracy.csv (model, source, vocab, split, seed, metric, value, 95% CI,
n, config, git commit).

Splits:
  fixed      the saved splits.json (train / val / test) - the headline numbers
  gss:<s>    GroupShuffleSplit 70/30 by group over ALL groups, seed s (s = 0..4)
  gkf:<f>    5-fold GroupKFold over ALL groups, fold f (f = 0..4)
In gss/gkf, 20% of the training groups become val (seeded); test groups are never seen.

Definitions (see docs/model.md):
- Decision points sit before each non-empty batch; the model sees earlier batches only.
- Accuracy at horizon k: each call of the k-th next batch is a target; top-n hit if its tool
  is among the model's n highest-ranked tools (``<end>`` excluded, ties by name).
- within-k recall: the call's tool was in the top-3 at any of the last k decision points.
- Batch metrics (target batches with 2+ calls): predicted set = tools with
  P(in next batch) >= 0.5, plus the top-1; exact-set match, set recall, mean set size.
- Calibration: per target call at k=1, confidence = top-1 probability, correct = top-1 is the
  call's tool; ECE over 10 equal-width bins and a reliability table.
- 95% CIs: 1,000 bootstrap resamples of test GROUPS (sessions of a group move together).
- Latency: p50/p99 of single ``predict(ctx, 1)`` + top-3 calls on up to 2,000 test contexts.
"""

from __future__ import annotations

import argparse
import copy
import csv
import json
import math
import random
import subprocess
import sys
import time
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.model_selection import GroupKFold, GroupShuffleSplit

from predictors.base import NextToolModel
from predictors.data import END, Episode, episodes, targets_ahead
from traces.load import PROCESSED, iter_session_dicts, load_sessions

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
CSV_PATH = RESULTS / "p1_accuracy.csv"
CSV_FIELDS = ["time", "model", "source", "vocab", "split", "seed", "metric", "value", "ci_lo",
              "ci_hi", "n", "config", "git_commit"]
KS = (1, 3, 5)
N_BOOT = 1000
N_BINS = 10
LATENCY_SAMPLES = 2000
SET_THRESHOLD = 0.5


def git_commit() -> str:
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, check=True,
                             capture_output=True, text=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"],
                               cwd=ROOT, check=True, capture_output=True, text=True).stdout
        return sha + ("-dirty" if dirty.strip() else "")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


# --------------------------------------------------------------------------- splits

def _carve_val(groups: list[str], seed: int, frac: float = 0.2) -> tuple[set[str], set[str]]:
    g = sorted(groups)
    random.Random(seed).shuffle(g)
    n_val = max(1, round(len(g) * frac))
    return set(g[n_val:]), set(g[:n_val])


def split_groups(source: str, split: str, root: Path = PROCESSED,
                 vocab: str | None = None) -> tuple[list[Episode], list[Episode], list[Episode]]:
    """(train, val, test) episodes for a split spec (see module docstring)."""
    sessions = load_sessions(source, None, root, vocab)
    if split == "fixed":
        saved = {d["session_id"]: d["split"] for d in iter_session_dicts(source, None, root)}
        by = defaultdict(list)
        for s in sessions:
            by[saved[s.session_id]].append(s)
        return episodes(by["train"]), episodes(by["val"]), episodes(by["test"])
    kind, _, num = split.partition(":")
    seed = int(num)
    groups = np.asarray([s.group for s in sessions])
    idx = np.arange(len(sessions))
    if kind == "gss":
        tr, te = next(GroupShuffleSplit(n_splits=1, test_size=0.3, random_state=seed)
                      .split(idx, groups=groups))
    elif kind == "gkf":
        folds = list(GroupKFold(n_splits=5, shuffle=True, random_state=0)
                     .split(idx, groups=groups))
        tr, te = folds[seed]
    else:
        raise ValueError(f"unknown split {split!r}")
    train_groups, val_groups = _carve_val(sorted({groups[i] for i in tr}), seed)
    train = [sessions[i] for i in tr if groups[i] in train_groups]
    val = [sessions[i] for i in tr if groups[i] in val_groups]
    test = [sessions[i] for i in te]
    assert not ({s.group for s in train} | {s.group for s in val}) & {s.group for s in test}
    return episodes(train), episodes(val), episodes(test)


# --------------------------------------------------------------------------- scoring

def top_n(probs: dict[str, float], n: int) -> list[str]:
    return [t for t, _ in sorted(((t, p) for t, p in probs.items() if t != END),
                                 key=lambda kv: (-kv[1], kv[0]))[:n]]


def _len_bucket(n: int) -> str:
    return "1-5" if n <= 5 else "6-20" if n <= 20 else "21-100" if n <= 100 else "101+"


@dataclass
class Records:
    """Per-target-call records, keyed by metric, with the target's group index."""

    num: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    grp: dict[str, list[int]] = field(default_factory=lambda: defaultdict(list))
    conf: list[float] = field(default_factory=list)
    correct: list[float] = field(default_factory=list)
    conf_grp: list[int] = field(default_factory=list)

    def add(self, metric: str, value: float, g: int) -> None:
        self.num[metric].append(value)
        self.grp[metric].append(g)


def score(model: NextToolModel, test: Sequence[Episode]) -> tuple[Records, list[str]]:
    groups = sorted({ep.group for ep in test})
    gidx = {g: i for i, g in enumerate(groups)}
    rec = Records()
    for ep in test:
        g = gidx[ep.group]
        ctxs = [ep.context(i) for i in range(ep.n_batches)]
        preds = {k: model.predict_many(ctxs, k) for k in KS}
        top3 = [set(top_n(p, 3)) for p in preds[1]]
        bprobs = None
        for i in range(ep.n_batches):
            for k in KS:
                tg = targets_ahead(ep, i, k)
                if not tg:
                    continue
                ranked = top_n(preds[k][i], 3)
                for t in tg:
                    rec.add(f"top1_k{k}", float(ranked[:1] == [t]), g)
                    rec.add(f"top3_k{k}", float(t in ranked), g)
            tools = ep.batch_tools(i)
            p1 = preds[1][i]
            first = top_n(p1, 1)
            conf = p1.get(first[0], 0.0) if first else 0.0
            for t in tools:
                hit = float(first == [t])
                rec.conf.append(conf)
                rec.correct.append(hit)
                rec.conf_grp.append(g)
                rec.add("nll_k1", -math.log(max(p1.get(t, 0.0), 1e-12)), g)
                for kk in (3, 5):
                    win = any(t in top3[j] for j in range(max(0, i - kk + 1), i + 1))
                    rec.add(f"within{kk}_recall", float(win), g)
                in3 = float(t in top3[i])
                sl = {"first_call": i == 0, "after_error": ep.errors_before[i]}
                for name, on in sl.items():
                    if on:
                        rec.add(f"slice_{name}_top1", hit, g)
                        rec.add(f"slice_{name}_top3", in3, g)
                bs = len(tools)
                bkey = "1" if bs == 1 else "2" if bs == 2 else "3plus"
                rec.add(f"slice_batch{bkey}_top1", hit, g)
                rec.add(f"slice_batch{bkey}_top3", in3, g)
                lkey = _len_bucket(ep.n_batches)
                rec.add(f"slice_len{lkey}_top1", hit, g)
                rec.add(f"slice_len{lkey}_top3", in3, g)
            if len(tools) >= 2:
                if bprobs is None:
                    bprobs = model.batch_probs_many(ctxs)
                bp = bprobs[i]
                pred = {t for t, p in bp.items() if p >= SET_THRESHOLD} | set(first)
                true = set(tools)
                rec.add("batch_exact", float(pred == true), g)
                rec.add("batch_set_recall", len(pred & true) / len(true), g)
                rec.add("batch_pred_size", float(len(pred)), g)
    return rec, groups


def ece(conf: np.ndarray, correct: np.ndarray, bins: int = N_BINS) -> float:
    if len(conf) == 0:
        return math.nan
    b = np.minimum((conf * bins).astype(int), bins - 1)
    cnt = np.bincount(b, minlength=bins)
    sc = np.bincount(b, weights=conf, minlength=bins)
    sh = np.bincount(b, weights=correct, minlength=bins)
    nz = cnt > 0
    return float(np.sum(np.abs(sc[nz] - sh[nz])) / cnt.sum())


def reliability(conf: np.ndarray, correct: np.ndarray, bins: int = N_BINS) -> list[dict[str, Any]]:
    b = np.minimum((conf * bins).astype(int), bins - 1)
    rows = []
    for i in range(bins):
        m = b == i
        rows.append({"bin": f"{i / bins:.1f}-{(i + 1) / bins:.1f}", "n": int(m.sum()),
                     "mean_conf": float(conf[m].mean()) if m.any() else math.nan,
                     "accuracy": float(correct[m].mean()) if m.any() else math.nan})
    return rows


def group_bootstrap(rec: Records, n_groups: int, n_boot: int = N_BOOT,
                    seed: int = 0) -> dict[str, tuple[float, float]]:
    """95% percentile CIs, resampling test groups with replacement."""
    if n_boot <= 0 or n_groups == 0:
        return {}
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, n_groups, size=(n_boot, n_groups))
    w = np.stack([np.bincount(d, minlength=n_groups) for d in draws]).astype(float)
    out: dict[str, tuple[float, float]] = {}
    for m, vals in rec.num.items():
        g = np.asarray(rec.grp[m])
        s = np.bincount(g, weights=np.asarray(vals), minlength=n_groups)
        c = np.bincount(g, minlength=n_groups).astype(float)
        den = w @ c
        stat = np.where(den > 0, (w @ s) / np.where(den > 0, den, 1), np.nan)
        out[m] = (float(np.nanpercentile(stat, 2.5)), float(np.nanpercentile(stat, 97.5)))
    if rec.conf:
        conf, cor, g = np.asarray(rec.conf), np.asarray(rec.correct), np.asarray(rec.conf_grp)
        b = np.minimum((conf * N_BINS).astype(int), N_BINS - 1)
        key = g * N_BINS + b
        size = n_groups * N_BINS
        cnt = np.bincount(key, minlength=size).reshape(n_groups, N_BINS)
        sc = np.bincount(key, weights=conf, minlength=size).reshape(n_groups, N_BINS)
        sh = np.bincount(key, weights=cor, minlength=size).reshape(n_groups, N_BINS)
        tc, ts, th = w @ cnt, w @ sc, w @ sh
        eces = np.abs(ts - th).sum(axis=1) / np.maximum(tc.sum(axis=1), 1)
        out["ece"] = (float(np.percentile(eces, 2.5)), float(np.percentile(eces, 97.5)))
    return out


def latency(model: NextToolModel, test: Sequence[Episode], n: int = LATENCY_SAMPLES,
            seed: int = 0) -> tuple[float, float]:
    ctxs = [ep.context(i) for ep in test for i in range(ep.n_batches)]
    if not ctxs:
        return math.nan, math.nan
    rng = random.Random(seed)
    sample = [ctxs[rng.randrange(len(ctxs))] for _ in range(min(n, len(ctxs)))]
    for c in sample[:50]:  # warm-up
        top_n(model.predict(c, 1), 3)
    times = []
    for c in sample:
        t0 = time.perf_counter_ns()
        top_n(model.predict(c, 1), 3)
        times.append((time.perf_counter_ns() - t0) / 1e6)
    a = np.asarray(times)
    return float(np.percentile(a, 50)), float(np.percentile(a, 99))


def model_config(model: NextToolModel) -> dict[str, Any]:
    cfg: dict[str, Any] = {}
    for attr in ("alpha", "weights", "min_count", "min_support", "edge_label",
                 "best_iteration", "val_ece", "calibration"):
        if hasattr(model, attr):
            v = getattr(model, attr)
            cfg[attr] = list(v) if isinstance(v, tuple) else v
    return cfg


def evaluate(model: NextToolModel, source: str, split: str = "fixed",
             vocab: str | None = None, *, root: Path = PROCESSED, n_boot: int | None = None,
             write: bool = True, csv_path: Path = CSV_PATH,
             test_source: str | None = None, refit: bool = True) -> dict[str, Any]:
    """Fit a fresh copy of ``model`` on the split's train part and score its test part.

    With ``test_source`` (cross-source), train and val come from ``source`` and the test set
    is ``test_source``'s test split (same split spec); both must share ``vocab``. Results are
    recorded with source "<source>-><test_source>".

    ``refit=False`` scores ``model`` as given; the caller guarantees it was fitted on this
    same split's train/val (used to score one fitted ranker with and without calibration).
    The fitted model is returned under ``"fitted"``.
    """
    train, val, test = split_groups(source, split, root, vocab)
    if test_source is not None:
        test = split_groups(test_source, split, root, vocab)[2]
    if not train or not test:
        raise ValueError(f"{source} {split}: empty train or test")
    if refit:
        m = copy.deepcopy(model)
        t0 = time.perf_counter()
        m.fit(train, val)
        fit_s = time.perf_counter() - t0
    else:
        m, fit_s = model, math.nan
    rec, groups = score(m, test)
    res: dict[str, Any] = {k: float(np.mean(v)) for k, v in rec.num.items()}
    res.update({f"n_{k}": len(v) for k, v in rec.num.items()})
    conf, cor = np.asarray(rec.conf), np.asarray(rec.correct)
    res["ece"] = ece(conf, cor)
    res["reliability"] = reliability(conf, cor)
    res["p50_ms"], res["p99_ms"] = latency(m, test)
    res["fit_s"] = fit_s
    res["n_test_groups"] = len(groups)
    res["n_test_sessions"] = len(test)
    boot = n_boot if n_boot is not None else (N_BOOT if split == "fixed" else 0)
    res["ci"] = group_bootstrap(rec, len(groups), boot)
    res["config"] = model_config(m)
    res["model"], res["vocab"], res["split"] = m.name, vocab, split
    res["fitted"] = m
    res["source"] = source if test_source is None else f"{source}->{test_source}"
    if write:
        _write(res, csv_path)
    return res


def _write(res: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    split = res["split"]
    seed = split.partition(":")[2] or "0"
    commit, now = git_commit(), datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    cfg = json.dumps(res["config"], sort_keys=True)
    with path.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if new:
            w.writeheader()
        for k, v in res.items():
            if not isinstance(v, (int, float)) or k.startswith("n_") and k != "n_test_groups":
                continue
            lo, hi = res["ci"].get(k, (math.nan, math.nan))
            n = res.get(f"n_{k}", "")
            w.writerow({"time": now, "model": res["model"], "source": res["source"],
                        "vocab": res["vocab"] or "", "split": split, "seed": seed,
                        "metric": k, "value": f"{v:.6g}", "ci_lo": f"{lo:.6g}",
                        "ci_hi": f"{hi:.6g}", "n": n, "config": cfg, "git_commit": commit})


def main(argv: list[str] | None = None) -> int:
    from predictors.baselines import ladder

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--source", required=True)
    p.add_argument("--vocab")
    p.add_argument("--models", default="all", help="comma list of model names, or 'all'")
    p.add_argument("--splits", default="fixed", help="comma list: fixed,gss,gkf")
    args = p.parse_args(argv)
    models = [m for m in ladder() if args.models == "all" or m.name in args.models.split(",")]
    specs: list[str] = []
    for s in args.splits.split(","):
        specs += [f"{s}:{i}" for i in range(5)] if s in ("gss", "gkf") else [s]
    for spec in specs:
        for m in models:
            r = evaluate(m, args.source, spec, args.vocab)
            ci = r["ci"].get("top1_k1")
            ci_s = f" [{ci[0]:.3f}, {ci[1]:.3f}]" if ci else ""
            print(f"{args.source:14s} {args.vocab or '-':13s} {spec:6s} {r['model']:18s} "
                  f"top1 {r['top1_k1']:.3f}{ci_s} top3 {r['top3_k1']:.3f} "
                  f"ece {r['ece']:.3f} p99 {r['p99_ms']:.3f} ms fit {r['fit_s']:.1f}s "
                  f"{r['config']}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
