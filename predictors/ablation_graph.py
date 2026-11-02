"""PRD ablation: next-tool accuracy with and without the graph prior, by training size.

    python -m predictors.ablation_graph

For each source, the fixed split's val and test sets are kept and the training set is
subsampled (by group, seeded) to n sessions. Pairs compared at every size:
markov1 vs graph_blend (PRD form) and ngram vs graph_blend_ngram. The prior uses workflow
edges on the synthetic traces (node known) and learned edges on real traces ("learned edges,
not workflow-defined"). alpha is tuned on val each time. Writes results/ablation_graph.csv.
"""

from __future__ import annotations

import argparse
import csv
import random
import sys

import numpy as np

from predictors.baselines import GraphBlend, Interpolated, Markov
from predictors.eval import RESULTS, git_commit, group_bootstrap, score, split_groups

SOURCES = [("synthetic_deterministic", None), ("synthetic_branching", None),
           ("synthetic_random_walk", None), ("tau_bench", None), ("tau2_bench", None),
           ("copilot_2026", "coding_common")]
SIZES = (10, 30, 100, 300, None)
SEEDS = (0, 1, 2)
CSV_PATH = RESULTS / "ablation_graph.csv"


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    commit = git_commit()
    rows = []
    for source, vocab in SOURCES:
        train, val, test = split_groups(source, "fixed", vocab=vocab)
        for n in SIZES:
            for seed in (SEEDS if n is not None else (0,)):
                sub = list(train)
                if n is not None:
                    if n >= len(train):
                        continue
                    sub = random.Random(seed).sample(train, n)
                for model in (Markov(1), GraphBlend("markov1"), Interpolated(),
                              GraphBlend("ngram")):
                    model.fit(sub, val)
                    rec, groups = score(model, test)
                    ci = group_bootstrap(rec, len(groups), 200)
                    r = {"source": source, "train_sessions": len(sub), "seed": seed,
                         "model": model.name, "alpha": getattr(model, "alpha", ""),
                         "edges": getattr(model, "edge_label", ""),
                         "top1_k1": float(np.mean(rec.num["top1_k1"])),
                         "top1_ci": "{:.3f}-{:.3f}".format(*ci["top1_k1"]),
                         "top3_k1": float(np.mean(rec.num["top3_k1"])),
                         "nll_k1": float(np.mean(rec.num["nll_k1"])), "git_commit": commit}
                    rows.append(r)
                    print(f"{source:24s} n={len(sub):5d} s={seed} {model.name:18s} "
                          f"top1 {r['top1_k1']:.3f} ({r['top1_ci']}) top3 {r['top3_k1']:.3f} "
                          f"nll {r['nll_k1']:.3f} alpha {r['alpha']}", flush=True)
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CSV_PATH.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
