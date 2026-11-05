"""Markdown tables from results/p1_accuracy.csv (latest run per model/source/vocab/split).

    python -m predictors.report                 # every source in the file
    python -m predictors.report --source tracelab --models markov1,ngram
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from predictors.eval import CSV_PATH

SMALL = {
    "tracelab": "16 test users (small: wide CIs)",
    "tau2_bench": "15 test groups (small: secondary evidence)",
    "copilot_2026": "398 test sessions (4 shards)",
}
ACC = [f"top{n}_k{k}" for k in (1, 3, 5) for n in (1, 3)]


def latest(path: Path = CSV_PATH) -> pd.DataFrame:
    if not path.exists() and path.with_suffix(".csv.gz").exists():
        path = path.with_suffix(".csv.gz")  # the committed, gzipped copy
    df = pd.read_csv(path, dtype={"vocab": str}, keep_default_na=False)
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    for c in ("ci_lo", "ci_hi"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    key = ["model", "source", "vocab", "split", "metric"]
    return df.sort_values("time").groupby(key, as_index=False).last()


def _cell(v: float, lo: float, hi: float) -> str:
    if pd.isna(lo):
        return f"{v:.3f}"
    return f"{v:.3f} [{lo:.3f}, {hi:.3f}]"


def tables(df: pd.DataFrame, source: str, vocab: str, models: list[str] | None) -> str:
    d = df[(df.source == source) & (df.vocab == vocab)]
    if models:
        d = d[d.model.isin(models)]
    order = [m for m in ["majority", "frequency", "markov1", "markov2", "markov3", "ngram",
                         "graph_blend", "graph_blend_ngram"] if m in set(d.model)]
    order += sorted(set(d.model) - set(order))
    fixed = d[d.split == "fixed"]
    title = f"{source}" + (f" (vocab {vocab})" if vocab else "")
    note = SMALL.get(source, "")
    lines = [f"### {title}" + (f" - {note}" if note else ""), "",
             "Fixed split, 95% group-bootstrap CI:", "",
             "| Model | " + " | ".join(ACC) + " |", "|---|" + "---|" * len(ACC)]
    for m in order:
        r = fixed[fixed.model == m].set_index("metric")
        if r.empty:
            continue
        cells = [_cell(r.value[a], r.ci_lo[a], r.ci_hi[a]) if a in r.index else "-"
                 for a in ACC]
        lines.append(f"| {m} | " + " | ".join(cells) + " |")
    lines += ["", "| Model | within-3 recall | ECE | NLL k=1 | p99 ms | "
              "GSS×5 top-1 k=1 | GKF×5 top-1 k=1 | GSS×5 top-3 k=1 | GKF×5 top-3 k=1 | config |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for m in order:
        r = fixed[fixed.model == m].set_index("metric")
        if r.empty:
            continue
        cv = []
        for metric in ("top1_k1", "top3_k1"):
            for kind in ("gss", "gkf"):
                v = d[(d.model == m) & d.split.str.startswith(kind) & (d.metric == metric)].value
                cv.append(f"{v.mean():.3f} ± {v.std(ddof=1):.3f} (n={len(v)})"
                          if len(v) > 1 else "-")
        cv = [cv[0], cv[1], cv[2], cv[3]]

        def g(metric: str, r: pd.DataFrame = r) -> str:
            return _cell(r.value[metric], r.ci_lo[metric], r.ci_hi[metric]) \
                if metric in r.index else "-"

        cfg = r.config.iloc[0] if "config" in r else ""
        lines.append(f"| {m} | {g('within3_recall')} | {g('ece')} | "
                     f"{r.value.get('nll_k1', float('nan')):.3f} | "
                     f"{r.value.get('p99_ms', float('nan')):.3f} | " + " | ".join(cv)
                     + f" | `{cfg}` |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source")
    p.add_argument("--models")
    args = p.parse_args(argv)
    df = latest()
    models = args.models.split(",") if args.models else None
    pairs = sorted({(s, v) for s, v in zip(df.source, df.vocab, strict=True)
                    if not args.source or s == args.source})
    for s, v in pairs:
        print(tables(df, s, v, models))
    return 0


if __name__ == "__main__":
    sys.exit(main())
