"""Phase 4 full runs: LightGBM ranker vs Markov-1 / Markov-3 / n-gram on every source.

    nice -n 10 python -m predictors.phase4                     # everything (fixed + GKF + cross)
    python -m predictors.phase4 --sources tau_bench --splits fixed
    python -m predictors.phase4 --report                       # markdown tables from the CSVs

Per (source, split) the baselines and the ranker go through ``predictors.eval.evaluate``
(rows in results/p1_accuracy.csv). ``lgbm_ranker`` is scored in the configured
``calibration`` mode (default ``none``: raw scores); the same fitted model is then scored in
the other mode (``lgbm_ranker_isotonic`` or ``lgbm_ranker_raw``), so the TEST-set ECE with and
without isotonic comes from one fitted model. VAL cannot choose between them: the isotonic
map is fitted on VAL, so its VAL ECE is in-sample. On the fixed split two more CSVs are
written, each row carrying the ranker config and the git commit:

- results/p4_ranker.csv: per-candidate (in-next-batch) ECE on TEST before/after
  calibration, its VAL counterpart, best iteration, fit time.
- results/p4_importance.csv: top-15 features by total gain.

Cross-source: trained on TraceLab (coding_common vocabulary), tested on Copilot's test split.
"""

from __future__ import annotations

import argparse
import copy
import csv
import json
import random
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from predictors.baselines import Interpolated, Markov
from predictors.eval import RESULTS, evaluate, git_commit, split_groups
from predictors.lgbm_ranker import LGBMRanker, _row_ece

SOURCES: list[tuple[str, str | None]] = [
    ("tracelab", None), ("tracelab", "coding_common"), ("copilot_2026", "coding_common"),
    ("tau_bench", None), ("tau2_bench", None), ("synthetic_branching", None),
    ("synthetic_deterministic", None), ("synthetic_random_walk", None),
]
CROSS = (("tracelab", "coding_common"), "copilot_2026")
RANKER_CSV = RESULTS / "p4_ranker.csv"
IMPORTANCE_CSV = RESULTS / "p4_importance.csv"
MAX_ECE_POINTS = 30000


def baselines() -> list:
    return [Markov(1), Markov(3), Interpolated()]


def _append(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        if new:
            w.writeheader()
        w.writerows(rows)


def test_row_ece(r: LGBMRanker, source: str, split: str, vocab: str | None,
                 seed: int = 0) -> dict[str, float]:
    """ECE of per-candidate in-next-batch probabilities on TEST decision points."""
    test = split_groups(source, split, vocab=vocab)[2]
    pts = [(ep, i) for ep in test for i in range(ep.n_batches + 1)]
    if len(pts) > MAX_ECE_POINTS:
        pts = random.Random(seed).sample(pts, MAX_ECE_POINTS)
    assert r.base is not None
    x, y, _ = r._dataset(r.base, [(ep.context(i), r._truth(ep, i)) for ep, i in pts])
    raw = r._raw(x)
    return {"test_row_ece_raw": _row_ece(raw, y),
            "test_row_ece_cal": _row_ece(r.calibrator(raw), y),
            "test_rows": float(len(y)), "test_points": float(len(pts))}


def _line(r: dict[str, Any]) -> str:
    ci = r["ci"].get("top1_k1")
    ci_s = f" [{ci[0]:.3f}, {ci[1]:.3f}]" if ci else ""
    return (f"{r['source']:30s} {r['vocab'] or '-':13s} {r['split']:6s} {r['model']:16s} "
            f"top1 {r['top1_k1']:.3f}{ci_s} top3 {r['top3_k1']:.3f} ece {r['ece']:.3f} "
            f"nll {r['nll_k1']:.3f} p99 {r['p99_ms']:.3f} ms fit {r['fit_s']:.1f}s")


def run_one(source: str, vocab: str | None, split: str,
            test_source: str | None = None) -> None:
    for m in baselines():
        print(_line(evaluate(m, source, split, vocab, test_source=test_source)), flush=True)
    res = evaluate(LGBMRanker.from_config(), source, split, vocab, test_source=test_source)
    print(_line(res), flush=True)
    r: LGBMRanker = res["fitted"]
    alt = copy.copy(r)
    alt.calibration = "isotonic" if r.calibration == "none" else "none"
    alt.name = "lgbm_ranker_isotonic" if alt.calibration == "isotonic" else "lgbm_ranker_raw"
    print(_line(evaluate(alt, source, split, vocab, test_source=test_source, refit=False)),
          flush=True)
    if split != "fixed" or test_source is not None:
        return
    commit, now = git_commit(), datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    cfg = json.dumps(r.cfg, sort_keys=True)
    ece = test_row_ece(r, source, split, vocab)
    _append(RANKER_CSV, [{
        "time": now, "source": source, "vocab": vocab or "", "split": split,
        "test_row_ece_raw": f"{ece['test_row_ece_raw']:.6g}",
        "test_row_ece_cal": f"{ece['test_row_ece_cal']:.6g}",
        "val_row_ece_raw": f"{r.val_ece.get('raw', float('nan')):.6g}",
        "val_row_ece_cal": f"{r.val_ece.get('calibrated', float('nan')):.6g}",
        "test_points": int(ece["test_points"]), "test_rows": int(ece["test_rows"]),
        "best_iteration": r.best_iteration, "fit_s": f"{res['fit_s']:.1f}",
        "config": cfg, "git_commit": commit}])
    imp = r.feature_importance(15)
    total = float(np.sum(r.booster.feature_importance("gain", iteration=r.best_iteration)))
    _append(IMPORTANCE_CSV, [{
        "time": now, "source": source, "vocab": vocab or "", "rank": i + 1, "feature": f,
        "gain": f"{g:.6g}", "gain_share": f"{g / total if total else 0.0:.4f}",
        "config": cfg, "git_commit": commit} for i, (f, g) in enumerate(imp)])
    print(f"  test row ECE raw {ece['test_row_ece_raw']:.4f} -> cal "
          f"{ece['test_row_ece_cal']:.4f}; top features "
          f"{[f for f, _ in imp[:5]]}", flush=True)


# --------------------------------------------------------------------------- report
MODELS = ["markov1", "markov3", "ngram", "lgbm_ranker"]


def report() -> str:
    from predictors.report import latest

    df = latest()
    out = []
    fx = df[df.split == "fixed"]
    gkf = df[df.split.str.startswith("gkf")]

    def cell(src: str, voc: str, model: str, metric: str, ci: bool = True) -> str:
        r = fx[(fx.source == src) & (fx.vocab == voc) & (fx.model == model)
               & (fx.metric == metric)]
        if r.empty:
            return "-"
        v, lo, hi = r.iloc[0][["value", "ci_lo", "ci_hi"]]
        return f"{v:.3f} [{lo:.2f},{hi:.2f}]" if ci and not pd.isna(lo) else f"{v:.3f}"

    def mode(src: str, voc: str, calibration: str, metric: str) -> str:
        """Latest fixed-split value of the ranker scored with ``calibration`` (none|isotonic).

        ``lgbm_ranker`` rows carry their mode in the config; rows from before the switch
        have none recorded and were isotonic.
        """
        r = fx[(fx.source == src) & (fx.vocab == voc) & (fx.metric == metric)
               & fx.model.isin(["lgbm_ranker", "lgbm_ranker_raw", "lgbm_ranker_isotonic"])]
        def m(row: pd.Series) -> str:
            if row.model != "lgbm_ranker":
                return "none" if row.model == "lgbm_ranker_raw" else "isotonic"
            return str(json.loads(row.config or "{}").get("calibration", "isotonic"))
        r = r[r.apply(m, axis=1) == calibration] if len(r) else r
        return "-" if r.empty else f"{r.sort_values('time').iloc[-1].value:.3f}"

    def gk(src: str, voc: str, model: str) -> str:
        r = gkf[(gkf.source == src) & (gkf.vocab == voc) & (gkf.model == model)
                & (gkf.metric == "top1_k1")]
        if len(r) < 5:
            return "-"
        return f"{r.value.mean():.3f} ± {r.value.std(ddof=1):.3f}"

    out.append("Top-1 at k=1, fixed split, 95% group-bootstrap CI; GKF = mean ± sd over 5 folds")
    out.append("")
    out.append("| Source | Markov-1 | Markov-3 | n-gram | Ranker | Ranker top-3 | "
               "GKF n-gram | GKF ranker |")
    out.append("|---|---|---|---|---|---|---|---|")
    for src, voc in [*SOURCES, (f"{CROSS[0][0]}->{CROSS[1]}", CROSS[0][1])]:
        v = voc or ""
        top1 = " | ".join(cell(src, v, m, "top1_k1") for m in MODELS)
        out.append(f"| {src} {voc or ''} | {top1} | "
                   f"{cell(src, v, 'lgbm_ranker', 'top3_k1', False)} | "
                   f"{gk(src, v, 'ngram')} | {gk(src, v, 'lgbm_ranker')} |")
    out.append("")
    out.append("Shipped: raw scores (`calibration: none`). TEST ECE is shown both ways; VAL "
               "cannot choose, because the isotonic map is fitted on VAL (its VAL ECE is "
               "in-sample).")
    out.append("")
    out.append("| Source | top-1 ECE raw -> iso | NLL raw -> iso | row ECE test raw -> iso "
               "| row ECE val (iso, in-sample) | p99 ms ranker | p99 ms n-gram |")
    out.append("|---|---|---|---|---|---|---|")
    rk = pd.read_csv(RANKER_CSV, dtype={"vocab": str}, keep_default_na=False) \
        if RANKER_CSV.exists() else pd.DataFrame()
    for src, voc in SOURCES:
        v = voc or ""
        e = rk[(rk.source == src) & (rk.vocab == v)].tail(1) if len(rk) else rk
        row_ece = (f"{float(e.test_row_ece_raw.iloc[0]):.4f} -> "
                   f"{float(e.test_row_ece_cal.iloc[0]):.4f}" if len(e) else "-")
        val_ece = f"{float(e.val_row_ece_cal.iloc[0]):.4f}" if len(e) else "-"
        out.append(
            f"| {src} {voc or ''} | {mode(src, v, 'none', 'ece')} -> "
            f"{mode(src, v, 'isotonic', 'ece')} | "
            f"{mode(src, v, 'none', 'nll_k1')} -> "
            f"{mode(src, v, 'isotonic', 'nll_k1')} | {row_ece} | {val_ece} | "
            f"{cell(src, v, 'lgbm_ranker', 'p99_ms', False)} | "
            f"{cell(src, v, 'ngram', 'p99_ms', False)} |")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--sources", default="all",
                   help="comma list of source or source/vocab, or 'all'")
    p.add_argument("--splits", default="fixed,gkf")
    p.add_argument("--no-cross", action="store_true")
    p.add_argument("--report", action="store_true")
    args = p.parse_args(argv)
    if args.report:
        print(report())
        return 0
    want = None if args.sources == "all" else set(args.sources.split(","))
    specs: list[str] = []
    for s in args.splits.split(","):
        specs += [f"{s}:{i}" for i in range(5)] if s in ("gss", "gkf") else [s]
    for src, voc in SOURCES:
        if want is not None and src not in want and f"{src}/{voc}" not in want:
            continue
        for spec in specs:
            run_one(src, voc, spec)
    if not args.no_cross:
        (src, voc), tgt = CROSS
        run_one(src, voc, "fixed", test_source=tgt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
