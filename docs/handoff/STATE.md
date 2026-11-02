# Prediction engine: state of work (branch feat/model)

Last updated: 2026-11-02 (after Phase 4 code). Local commits only; nothing pushed or merged.

## Commits

| Commit | Contents |
|---|---|
| 408868f | Phase 0: TraceLab shell first-word split, `coding_common` vocabulary, integrity tests, pipeline `--keep-splits`, Copilot 4 shards, synthetic PRD traces, PRD excerpts |
| 3273927 | Phases 1-2: evaluation harness, baseline ladder, paper protocol, graph-prior ablation, leakage property test |
| f597c7b | Phase 3: TransitionGraph, poisoning guards, gated rebuild, end-node fix |
| 2a3746c | Phase 4 code: LightGBM re-ranker, isotonic calibrator (JSON), graph-derived base models, cross-source evaluation; unit-tested on samples only |
| 2d03cba | Phase 2 results (`results/p1_accuracy.csv.gz`, `ablation_graph.csv`, `paper_protocol.csv`); rows record `37c400d-dirty` because they were produced before commit 3273927, which holds that code |

## Done

- **Phase 0.** TraceLab shell 63.6% -> `shell_other` 29.2%. The class comes from the first
  executable name only; setup words (`cd`, `source`, `export`, ...) are skipped, `sed`/`nl`
  count as `read_file`, and `npm`/`cargo`/`go`/`make` count as `build` (sub-commands are not
  in the data). `custom_tool` folded into `other` (15-class cap). `coding_common` keeps only
  distinctions both TraceLab and Copilot can express (git -> shell; agent/web/user_io/schedule
  -> other). Integrity tests pass on all processed sources and samples.
- **Phase 0.4.** Synthetic deterministic (B), branching (A) and random-walk traces, 2,000
  sessions each, seed 0, think times from tau2 (A) / TraceLab (B, RW) train. Note: the PRD's
  branch probabilities give 38.5% full chains, not "about 85%"; the branch probabilities are
  used.
- **Copilot.** 4 of 22 shards in the local archive converted (1,219 sessions, 398 test); the
  original 296 split assignments are unchanged.
- **Phase 1.** `predictors/eval.py` (definitions in its docstring); leakage property test
  covers every model registered in `predictors/registry.py`.
- **Phase 2.** Baseline ladder on all sources, fixed split + 5 GSS seeds + 5-fold GKF.

## Phase 2 numbers (fixed split, top-1 at k=1, 95% group-bootstrap CI)

| Source | Majority | Markov-1 (PRD) | Markov-3 | n-gram | n-gram top-3 | GKF n-gram top-1 |
|---|---|---|---|---|---|---|
| TraceLab native (16 test users) | .243 [.17,.33] | .399 [.36,.47] | .466 [.44,.52] | .454 [.42,.51] | .789 | .473 ± .038 |
| TraceLab coding_common | .419 | .524 | .558 [.50,.60] | .549 | .868 | .569 ± .059 |
| Copilot coding_common (398 test sessions) | .352 [.33,.37] | .449 [.43,.47] | .495 [.48,.51] | .490 [.47,.51] | .880 | .499 ± .009 |
| tau-bench | .247 [.21,.28] | .547 [.51,.58] | .592 [.56,.63] | .607 [.57,.64] | .864 | .601 ± .025 |
| tau2-bench (15 test groups, secondary) | .468 [.35,.57] | .620 [.49,.73] | .588 | .620 [.49,.73] | .877 | .574 ± .110 |
| Synthetic B / A / RW | .250 / .443 / .234 | 1.000 / .918 / .614 | same | same | 1.000 / 1.000 / .949 | - |

- Higher-order context beats the PRD Markov-1 by about 5-7 top-1 points on TraceLab and
  tau-bench (beyond the CIs); about 4 points on Copilot; nothing separable on tau2.
- Graph prior: alpha tunes to 0 on every source at full data; it never changes top-1 at any
  training size; it improves log-loss with scarce data on synthetic traces (A, 10 sessions:
  1.015 -> 0.344).
- p99 latency of every baseline is at most 0.05 ms.
- Baselines predict about one tool per parallel batch (exact-set match .25 TraceLab, .45
  Copilot); the batch-set predictor is Phase 3.
- Paper protocol (tau-bench): our Majority .263 vs published .456 -> not comparable; see
  docs/benchmark_protocol.md.

## Phase 3 (done, committed f597c7b)

- `predictors/graph.py` `TransitionGraph`: one_step (node table first, then order 3..0
  back-off), k_step (within k), timing p10/p50/p90 with pair/next/global fallback, batch_next
  sets, copies, p_end, fleet_demand; `counts.json` (deterministic JSON, schema_version, train
  hash, vocabulary). Parameters in `configs/model/graph.yaml`.
- Guards: per-session cap per (context -> target) pair (config, default 3; `null` = off),
  min_support 3, gaps clipped to 1 h then winsorised p1/p99, sessions > 2,000 events or with
  non-finite times dropped.
- Finding: the per-session cap does not stop volume poisoning (sample: 100+ junk sessions
  moved top-1 0.408 -> 0.350). Added `rebuild()`: logged sessions down-weighted to <= 50% of
  base, and the rebuilt graph is kept only if val top-1 drops <= 1 point. Both layers tested.
  Per your instruction, no further poisoning work beyond what the red-team suite needs.
- Fix: the end decision point carries the node of the last call (what a live request would
  send); Phase 2 numbers unaffected.

## Phase 4 (code done, tested on samples only)

- `predictors/lgbm_ranker.py`: candidates = graph n-gram top-20 U graph-prior-blend top-5 U
  `<end>`; 29 features (one_step, k_step 2/3/5, n-gram, blend, expected calls, timing
  quantiles, p_end, last 1-5 tools, last batch size / error / service_ms, step, time since
  start, candidate frequency / recency / is-last, n-gram rank, node). Train rows use 3-fold
  group out-of-fold base features; early stopping and isotonic calibration on val. Outputs:
  `ranked` (sums to <= 1, explicit `<end>`) and `batch_probs` (calibrated, may sum > 1).
  Config `configs/model/ranker.yaml` (num_threads 4, seed 0).
- `predictors/graph_models.py`: the ranker's n-gram and graph-prior blend are computed from
  the TransitionGraph's guarded counts, so `counts.json` + `ranker.lgb` + `calibrator.json`
  + `ranker_meta.json` fully define the model (save/load tested, byte-identical retraining).
- `evaluate(..., test_source=...)` for the cross-source test (TraceLab -> Copilot).
- Sample smoke run (TraceLab sample, NOT a result): fit 3.4 s, top-1 0.451, top-3 0.821,
  p99 a few ms. No full training yet.

## Decisions made

- PRD excerpts in `docs/prd_excerpts.md`; our interpretations of FR-2, FR-3, cost and
  Scenario C are labelled as such. PDFs in docs/ are gitignored except the plan.
- Real traces have no workflow nodes: graph prior uses learned edges (min support 3),
  labelled "learned edges, not workflow-defined"; workflow edges only on synthetic traces.
- Small test sets are reported with group-bootstrap CIs plus 5-fold GroupKFold.
- `results/p1_accuracy.csv` is committed gzipped; chat exports in `docs/handoff/*.txt` are
  gitignored.
- Ranker candidates: n-gram top-20 U graph-prior top-5 (closed).
- No heavy jobs while E0 runs on the infra side; after E0, LightGBM uses num_threads=4.

## Next

After you confirm E0 is done:
1. Phase 4 full runs: ranker on every source (fixed split, CIs, GKF), top-15 importance,
   ECE before/after calibration on test, p99 latency, cross-source TraceLab -> Copilot drop.
2. Per-session cap measurement on full TraceLab (cap 3 vs 5 vs none); raise the cap if 3 costs
   more than 1 point of top-1.
Then Phase 5 (decisions).
