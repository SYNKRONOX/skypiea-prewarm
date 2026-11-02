# Prediction engine: state of work (branch feat/model)

Last updated: 2026-11-02. Local commits only; nothing pushed or merged.

## Commits

| Commit | Contents |
|---|---|
| 408868f | Phase 0: TraceLab shell first-word split, `coding_common` vocabulary, integrity tests, pipeline `--keep-splits`, Copilot 4 shards, synthetic PRD traces, PRD excerpts |
| 3273927 | Phases 1-2: evaluation harness, baseline ladder, paper protocol, graph-prior ablation, leakage property test |
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

## Decisions made

- PRD excerpts in `docs/prd_excerpts.md`; our interpretations of FR-2, FR-3, cost and
  Scenario C are labelled as such. PDFs in docs/ are gitignored except the plan.
- Real traces have no workflow nodes: graph prior uses learned edges (min support 3),
  labelled "learned edges, not workflow-defined"; workflow edges only on synthetic traces.
- Small test sets are reported with group-bootstrap CIs plus 5-fold GroupKFold.
- `results/p1_accuracy.csv` is committed gzipped; chat exports in `docs/handoff/*.txt` are
  gitignored.
- Open (awaiting answer): ranker candidates from n-gram (recommended) or Markov-3.

## Next

Phase 3 (`predictors/graph.py`, TransitionGraph with poisoning guards, counts.json export):
code and unit tests on small samples only. No heavy runs until E0 is done on the infra side.
