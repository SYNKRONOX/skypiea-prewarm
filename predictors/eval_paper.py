"""Re-run of the "Reading the Plan" evaluation protocol on tau-bench (docs/benchmark_protocol.md).

    python -m predictors.eval_paper

Protocol, as published:
- tau-bench retail + airline trajectories pooled, RAW tool names;
- tools seen fewer than 50 times in the pooled data are dropped (their calls are removed
  from the sequences);
- GroupShuffleSplit 70/30 by task, 5 seeds (0-4), mean +- std over seeds;
- Markov = the most frequent continuation: given the current tool, the most frequent tool
  observed k steps later in train (top-3 = the three most frequent); unseen current tool
  falls back to Majority;
- Majority = the most frequent label in train.
Positions: every call that has a current tool before it and a call k steps later.

We report our numbers beside the published ones and never tune towards them. This is a
separate protocol from predictors/eval.py (raw names, no batches, no val, no end token).
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from sklearn.model_selection import GroupShuffleSplit

from predictors.eval import RESULTS, git_commit
from traces.load import PROCESSED, load_sessions

PUBLISHED = {
    "majority": {1: (0.456, None), 3: (0.456, None), 5: (0.456, None)},
    "markov": {1: (0.638, 0.950), 3: (0.662, 0.916), 5: (0.287, 0.648)},
}
KS = (1, 3, 5)
MIN_COUNT = 50
SEEDS = range(5)
CSV_PATH = RESULTS / "paper_protocol.csv"
DOMAINS = ("retail", "airline")


def load_pooled(root: Path = PROCESSED) -> tuple[list[list[str]], list[str], dict[str, int]]:
    """Raw-name sequences of retail + airline, rare tools removed; with groups and counts."""
    seqs, groups = [], []
    for s in load_sessions("tau_bench", None, root):
        if s.group.split(":")[0] not in DOMAINS:
            continue
        seqs.append([c.raw_tool or c.tool for b in s.batches for c in b.calls])
        groups.append(s.group)
    counts = Counter(t for q in seqs for t in q)
    keep = {t for t, c in counts.items() if c >= MIN_COUNT}
    seqs = [[t for t in q if t in keep] for q in seqs]
    pairs = [(q, g) for q, g in zip(seqs, groups, strict=True) if q]
    return [q for q, _ in pairs], [g for _, g in pairs], dict(counts)


def run_seed(seqs: list[list[str]], groups: list[str], seed: int) -> dict[str, float]:
    idx = np.arange(len(seqs))
    tr, te = next(GroupShuffleSplit(n_splits=1, test_size=0.3, random_state=seed)
                  .split(idx, groups=np.asarray(groups)))
    train = [seqs[i] for i in tr]
    test = [seqs[i] for i in te]
    majority = Counter(t for q in train for t in q).most_common(1)[0][0]
    cont: dict[int, dict[str, Counter[str]]] = {k: defaultdict(Counter) for k in KS}
    for q in train:
        for i in range(len(q)):
            for k in KS:
                if i + k < len(q):
                    cont[k][q[i]][q[i + k]] += 1
    out: dict[str, float] = {}
    for k in KS:
        n = h_maj = h1 = h3 = 0
        for q in test:
            for i in range(len(q) - k):
                target = q[i + k]
                ranked = sorted(cont[k][q[i]].items(), key=lambda kv: (-kv[1], kv[0]))
                top = [t for t, _ in ranked[:3]] or [majority]
                n += 1
                h_maj += target == majority
                h1 += target == top[0]
                h3 += target in top
        out[f"majority_top1_k{k}"] = h_maj / n
        out[f"markov_top1_k{k}"] = h1 / n
        out[f"markov_top3_k{k}"] = h3 / n
        out[f"n_k{k}"] = n
    return out


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    seqs, groups, counts = load_pooled()
    dropped = sorted(t for t, c in counts.items() if c < MIN_COUNT)
    per_seed = [run_seed(seqs, groups, s) for s in SEEDS]
    commit, now = git_commit(), datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    new = not CSV_PATH.exists()
    with CSV_PATH.open("a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["time", "seed", "metric", "value", "git_commit"])
        for s, r in zip(SEEDS, per_seed, strict=True):
            for m, v in r.items():
                w.writerow([now, s, m, f"{v:.6g}", commit])
    print(f"sessions {len(seqs)}, tasks {len(set(groups))}, "
          f"calls {sum(len(q) for q in seqs)}, dropped tools (<{MIN_COUNT}): {dropped}")
    print("| Model | k | Ours top-1 (mean ± std) | Published top-1 | Ours top-3 "
          "| Published top-3 |")
    print("|---|---|---|---|---|---|")
    for k in KS:
        for model in ("majority", "markov"):
            t1 = np.array([r[f"{model}_top1_k{k}"] for r in per_seed])
            pub1, pub3 = PUBLISHED[model][k]
            if model == "markov":
                t3 = np.array([r[f"markov_top3_k{k}"] for r in per_seed])
                ours3 = f"{t3.mean():.3f} ± {t3.std(ddof=1):.3f}"
            else:
                ours3 = "-"
            print(f"| {model} | {k} | {t1.mean():.3f} ± {t1.std(ddof=1):.3f} | {pub1:.3f} | "
                  f"{ours3} | {'-' if pub3 is None else f'{pub3:.3f}'} |")
    maj = np.mean([r["majority_top1_k1"] for r in per_seed])
    verdict = "comparable" if abs(maj - 0.456) <= 0.03 else "NOT comparable"
    print(f"\nOur Majority (k=1) {maj:.3f} vs published 0.456 -> {verdict} (tolerance ±0.03)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
