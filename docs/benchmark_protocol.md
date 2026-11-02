# Benchmark protocol: "Reading the Plan" on tau-bench

`python -m predictors.eval_paper` re-runs the published next-tool protocol so our numbers can
be placed next to the paper's. It is separate from our own harness (`predictors/eval.py`),
which uses tool classes, batch-level decision points, a validation split and group-bootstrap
confidence intervals.

## Protocol as implemented

| Step | Paper | Ours |
|---|---|---|
| Data | tau-bench retail + airline, pooled | `historical_trajectories/` of tau-bench v1: gpt-4o and sonnet-3.5-new, retail and airline (4 files), after our pipeline (1,928 of 1,980 trajectories; 52 with fewer than 2 tool calls or none are dropped) |
| Tool names | raw | raw (`raw_tool`) |
| Rare tools | dropped if seen < 50 times | same; dropped: `list_all_airports`, `modify_pending_order_payment`, `send_certificate`, `update_reservation_passengers` (their calls are removed from the sequences) |
| Split | GroupShuffleSplit 70/30 by task, 5 seeds | same (`sklearn`, `random_state` 0-4), group = `<domain>:<task_id>` |
| Markov | most frequent continuation | most frequent tool observed exactly k calls after the current tool in train; top-3 = three most frequent; unseen current tool -> Majority |
| Majority | most frequent label | most frequent tool in train, scored at the same positions as Markov |
| Positions | not stated in the excerpt we have | every call that has a current tool before it and a call k steps later |

## Results (5 seeds, mean ± std)

| Model | k | Ours top-1 | Published top-1 | Ours top-3 | Published top-3 |
|---|---|---|---|---|---|
| Majority | 1 | 0.263 ± 0.034 | 0.456 | - | - |
| Majority | 3 | 0.273 ± 0.042 | 0.456 | - | - |
| Majority | 5 | 0.166 ± 0.030 | 0.456 | - | - |
| Markov | 1 | 0.515 ± 0.029 | 0.638 | 0.774 ± 0.026 | 0.950 |
| Markov | 3 | 0.350 ± 0.032 | 0.662 | 0.631 ± 0.028 | 0.916 |
| Markov | 5 | 0.239 ± 0.016 | 0.287 | 0.493 ± 0.035 | 0.648 |

**Verdict: not comparable.** Our Majority is 0.263, outside the ±0.03 band around the
published 0.456, so the two setups differ and the Markov numbers cannot be compared either.

## What differs (what we checked)

- **The majority share itself.** In our data no subset reaches a 0.456 majority share. The
  most frequent raw tool per file covers 0.314 (gpt-4o retail), 0.324 (gpt-4o airline),
  0.342 (sonnet retail) and 0.377 (sonnet airline) of calls; pooled, `get_order_details` is
  0.244. So the gap is not explained by which trajectory files are pooled.
- **Majority constant across k.** The paper reports 0.456 at k = 1, 3 and 5. A majority
  scored position-wise, as ours is, changes with k (0.263, 0.273, 0.166) because the set of
  positions changes. This suggests the paper's Majority is computed on a different label
  set (for example per trajectory, on a different tool vocabulary, or on a different subset
  of positions), which we cannot reconstruct from the excerpt.
- **Data version.** The paper may use its own agent runs or a different tau-bench release
  rather than the published `historical_trajectories`.

We did not adjust anything to move towards the published numbers. If the paper's exact
position set or data files become available, the script can be updated and re-run.
