# Prediction engine: state of work (branch feat/model)

Last updated: 2026-11-02 (after Phase 4 full runs). Local commits only; nothing pushed or merged.

## Commits

| Commit | Contents |
|---|---|
| 408868f | Phase 0: TraceLab shell first-word split, `coding_common` vocabulary, integrity tests, pipeline `--keep-splits`, Copilot 4 shards, synthetic PRD traces, PRD excerpts |
| 3273927 | Phases 1-2: evaluation harness, baseline ladder, paper protocol, graph-prior ablation, leakage property test |
| f597c7b | Phase 3: TransitionGraph, poisoning guards, gated rebuild, end-node fix |
| 2a3746c | Phase 4 code: LightGBM re-ranker, isotonic calibrator (JSON), graph-derived base models, cross-source evaluation; unit-tested on samples only |
| 7d23f85 | Phase 4 runner `predictors/phase4.py`; `evaluate(refit=False)` |
| 06c2e8e | Ranker fix: isotonic ties broken by raw score (calibration never reorders) |
| (this) | Phase 4 results: `results/p4_report.md`, `p4_ranker.csv`, `p4_importance.csv`, `p1_accuracy.csv.gz`; `docs/model.md` contract alignment |
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

## Phase 4 (done: full runs after E0, num_threads 4)

- `predictors/lgbm_ranker.py`: candidates = graph n-gram top-20 U graph-prior-blend top-5 U
  `<end>`; 29 features; 3-fold group out-of-fold base features on train; early stopping and
  isotonic calibration on val; `ranked` (sums to <= 1) and `batch_probs` (marginal).
  Base models come from the TransitionGraph's guarded counts (`predictors/graph_models.py`).
- Fix found in the smoke run: isotonic steps tied distinct raw scores and ties fell back to
  tool names (tau2 top-1 .520 -> .453). Ties are now broken by the raw score; test added.
- Results (`results/p4_report.md`; fixed split top-1, 95% group CI; GKF mean ± sd):

| Source | n-gram | Ranker | GKF n-gram | GKF ranker | ranker > n-gram folds |
|---|---|---|---|---|---|
| TraceLab native | .454 [.42,.51] | .529 [.48,.57] | .473 ± .038 | .535 ± .052 | 5/5 |
| TraceLab coding_common | .549 | .572 | .569 | .584 | 5/5 |
| Copilot coding_common | .490 [.47,.51] | .506 [.49,.52] | .499 ± .009 | .516 ± .011 | 5/5 |
| tau-bench | .607 | .618 | .601 | .611 | 4/5 |
| tau2 (15 groups) | .620 [.49,.73] | .520 [.44,.58] | .574 | .599 | 5/5 (fixed split: worse) |
| Synthetic A / B / RW | .918 / 1 / .614 | same | same | same | 0/5 (no headroom) |
| TraceLab -> Copilot | .437 | .458 | - | - | cross-source drop: ranker -4.8, n-gram -5.3 points |

- Calibration: raw LightGBM (binary log-loss) is already calibrated on test (row ECE
  .002-.004 on real sources); val-fitted isotonic makes it slightly worse there (.003-.006)
  and helps only on tau2 (.026 -> .019) and synthetic A (.030 -> .011). Top-1 ECE moves both
  ways. Open question for the user: keep isotonic or ship raw scores.
- Ranker p99 latency 1.7-6.6 ms (single predict + top-3, Python feature build; one noisy
  10.1 ms raw-mode run); n-gram p99 <= 0.07 ms. Fit: 92 s TraceLab, 14 s Copilot.
- Top features: n-gram, one_step, candidate frequency in the session; TraceLab native also
  uses last_service_ms and candidate recency.

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

## Contract

- `docs/model.md` lists every difference between the original service design and the infra
  contract `docs/predictor_contract.md` (feat/infra 18a711f), and proposals P1-P8 (not
  applied). The service (Phase 9) will conform to the contract exactly: `POST /decide`.

## Next

1. (Waiting for the user's go after the Phase 4 report.) Per-session cap measurement on full
   TraceLab (cap 3 vs 5 vs none); raise the cap if 3 costs more than 1 point of top-1.
2. Phase 5 decisions, then Phase 6 sim on full data with `configs/cold_start.yaml` (E0 kind,
   committed on feat/infra c21a567, a lower bound). The sim report must add a hideable
   fraction per source and tool class (think gap > cold start, cached and uncached) and the
   cold rate split by hideable / non-hideable.

## Rules for the next session (from the user)

- Worktree `~/skypiea-model`, branch `feat/model` only; local commits, never push or merge.
  Git has no identity configured: reuse the last commit's author with `git -c user.name=...
  -c user.email=...`; never change git config; never print the author email.
- Only create or modify: predictors/, traces/, sim/, services/predictor/, configs/{tool_classes,
  vocab,model}/, data/samples/, models/README.md, tests/, tools_audit/, mk/model.mk,
  requirements-model.txt, docs/{model,benchmark_protocol,model_readme_section,redteam}.md,
  docs/handoff/. Never edit README.md, the root Makefile, requirements.txt, tools/, deploy/,
  infra/, controller/, shaper/, configs/cold_start.yaml (infra session owns those, in
  `~/skypiea-infra` on feat/infra).
- Before each commit: ruff + pytest pass; nothing from data/raw or data/processed, no .venv,
  caches or model binaries; ask before committing any file over 1 MB; grep for secrets/emails
  and never print them; no PDFs except the plan; no chat exports.
- Data privacy: never store command or prompt text; no string over 64 chars; no pickle,
  joblib, eval, exec, os.system or shell=True in services/ and predictors/.
- Science: train on train, tune on val, report on test; fixed seeds; every results CSV
  records config and git commit; never invent numbers; never tune to hit a published number;
  same error twice -> stop and show the user.
- Compute: `nice -n 10`, LightGBM num_threads 4, single process; the laptop is shared with
  Docker and a kind cluster.
- Stop at each STOP point and wait. No more poisoning guards beyond what the red-team needs.
- Run with `.venv/bin/python` (PYTHONPATH=$PWD for scripts). Phase 4 runner:
  `python -m predictors.phase4 [--report]`; log in data/processed/_staging/.
- Status now: Phase 4 reported; waiting for the user's go on step 2 (cap measurement) and
  the raw-vs-isotonic decision.
