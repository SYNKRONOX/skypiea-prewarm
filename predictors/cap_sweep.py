"""Per-session cap measurement on full TraceLab (cap 3 vs 5 vs none).

    nice -n 10 python -m predictors.cap_sweep                  # fixed split + 5-fold GKF
    python -m predictors.cap_sweep --report

The cap bounds how often one session can count any (context -> target) pair in the
transition graph (configs/model/graph.yaml ``per_session_cap``). For each cap, the graph
n-gram (the ranker's main base feature) and the full ranker are fitted on train, tuned on val
and scored on test through ``predictors.eval.evaluate``. Rows go to results/p4_cap.csv (model
names ``graph_ngram_cap<c>`` / ``lgbm_ranker_cap<c>``, with ``none`` for no cap). Decision rule
(user): raise the cap if cap 3 costs more than 1 point of top-1 against the best cap.
"""

from __future__ import annotations

import argparse
import copy
import sys
from collections.abc import Sequence

import pandas as pd

from predictors.base import NextToolModel
from predictors.data import Context, Episode
from predictors.eval import RESULTS, evaluate
from predictors.graph import TransitionGraph
from predictors.graph_models import GraphNgram
from predictors.lgbm_ranker import LGBMRanker

CAPS: list[int | None] = [3, 5, None]
CSV = RESULTS / "p4_cap.csv"
SOURCE, VOCAB = "tracelab", None


def _tag(cap: int | None) -> str:
    return "none" if cap is None else str(cap)


class CappedNgram(NextToolModel):
    """Graph n-gram over a TransitionGraph built with a given per-session cap."""

    def __init__(self, cap: int | None) -> None:
        self.per_session_cap = cap
        self.name = f"graph_ngram_cap{_tag(cap)}"
        self.m: GraphNgram | None = None

    def fit(self, train: Sequence[Episode], val: Sequence[Episode]) -> CappedNgram:
        g = TransitionGraph.from_config(per_session_cap=self.per_session_cap).fit(train)
        self.m = GraphNgram(g).fit(train, val)
        self.weights, self.mean_batch_size = self.m.weights, self.m.mean_batch_size
        return self

    def predict(self, ctx: Context, k: int = 1) -> dict[str, float]:
        assert self.m is not None
        return self.m.predict(ctx, k)

    def batch_probs(self, ctx: Context) -> dict[str, float]:
        assert self.m is not None
        return self.m.batch_probs(ctx)


def ranker(cap: int | None) -> LGBMRanker:
    r = LGBMRanker.from_config()
    r.cfg = {**copy.deepcopy(r.cfg), "graph": {"per_session_cap": cap}}
    r.name = f"lgbm_ranker_cap{_tag(cap)}"
    return r


def report() -> str:
    df = pd.read_csv(CSV, dtype={"vocab": str}, keep_default_na=False)
    df = df[df.metric == "top1_k1"].sort_values("time")
    df = df.groupby(["model", "split"], as_index=False).last()
    out = ["Per-session cap, TraceLab native, top-1 at k=1 (fixed split with 95% group CI; "
           "GKF mean ± sd over 5 folds)", "",
           "| Model | cap | fixed | GKF | fixed vs cap 3 | GKF vs cap 3 |",
           "|---|---|---|---|---|---|"]
    for fam in ("graph_ngram", "lgbm_ranker"):
        base_fx = base_gk = None
        for cap in CAPS:
            m = df[df.model == f"{fam}_cap{_tag(cap)}"]
            fx = m[m.split == "fixed"]
            gk = m[m.split.str.startswith("gkf")].value.astype(float)
            fxv = float(fx.value.iloc[0]) if len(fx) else float("nan")
            gkv = float(gk.mean()) if len(gk) == 5 else float("nan")
            if cap == 3:
                base_fx, base_gk = fxv, gkv
            ci = (f" [{float(fx.ci_lo.iloc[0]):.3f},{float(fx.ci_hi.iloc[0]):.3f}]"
                  if len(fx) else "")
            gks = f"{gkv:.3f} ± {gk.std(ddof=1):.3f}" if len(gk) == 5 else "-"
            out.append(f"| {fam} | {_tag(cap)} | {fxv:.3f}{ci} | {gks} | "
                       f"{100 * (fxv - base_fx):+.1f} pt | {100 * (gkv - base_gk):+.1f} pt |")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--splits", default="fixed,gkf")
    p.add_argument("--models", default="graph_ngram,lgbm_ranker")
    p.add_argument("--report", action="store_true")
    args = p.parse_args(argv)
    if args.report:
        print(report())
        return 0
    specs: list[str] = []
    for s in args.splits.split(","):
        specs += [f"{s}:{i}" for i in range(5)] if s in ("gss", "gkf") else [s]
    fams = args.models.split(",")
    for spec in specs:
        for cap in CAPS:
            ms: list[NextToolModel] = []
            if "graph_ngram" in fams:
                ms.append(CappedNgram(cap))
            if "lgbm_ranker" in fams:
                ms.append(ranker(cap))
            for m in ms:
                r = evaluate(m, SOURCE, spec, VOCAB, csv_path=CSV)
                print(f"{spec:6s} {r['model']:20s} top1 {r['top1_k1']:.4f} "
                      f"top3 {r['top3_k1']:.4f} fit {r['fit_s']:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
