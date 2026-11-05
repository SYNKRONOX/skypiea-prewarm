# Numbers we say out loud, and where each comes from

Each row gives the number, its source (a PRD line in `docs/prd_excerpts.md`, which holds the
PRD text verbatim, or a file and the commit that last changed it), and one sentence on why.
**UNSOURCED** marks a number that is a design choice or an assumption, with no requirement or
measurement behind it. Result numbers are copied from `docs/results_summary.md`, which is
generated from the committed result files (`make -f mk/model.mk results-summary`). If a row
here ever disagrees with that file, the file is right. "infra" means the feat/infra branch
(`~/skypiea-infra`).

## Requirements and settings

| Number | What it is | Source | Why |
|---|---|---|---|
| 50 ms | FR-1: top-3 predictions within <= 50 ms. NFR-1: the engine adds <= 50 ms to p99 end-to-end tool-call latency at 500 concurrent sessions | PRD FR-1 and NFR-1, `docs/prd_excerpts.md` lines 10-12 and 28-29 (408868f) | The prediction must cost far less than the cold start it hides. |
| 13.2 / 15.9 ms | Measured service p99 at 75 / 150 req/s, open loop, 1 worker | `results/loadtest.csv` (1a72c23), bundle lgbm-coding-69d7b94 | Evidence for the 50 ms budget, but measured on the laptop for the service alone, not end-to-end on AWS. |
| 75 req/s ~ 500 sessions | Load-test rate used to stand for 500 sessions | `services/predictor/loadtest.py` line 8: "500 sessions x one call per ~6.7 s" | **UNSOURCED**: the one call per ~6.7 s per session is an assumption. NFR-4's 8 calls per session over 30 minutes would be only about 2 req/s, and NFR-2 uses 100 calls/s. |
| tau 0.6 | Threshold at which FR-2 is judged | PRD FR-2, lines 13-15 (408868f); also the Markov-1 baseline's tau (`configs/model/sim.yaml` `markov_tau`, d84c894) | The PRD fixes the threshold for the acceptance test, so baselines are compared at it. |
| 80% | FR-2 target: pod Ready before the call in >= 80% of replayed trajectories at tau 0.6 | PRD FR-2, line 15 | The acceptance bar. We report it per call and per trajectory (our interpretation, `docs/prd_excerpts.md` "Our interpretation"). |
| tau 0.4 | The support profile's fixed threshold | `configs/model/profiles.yaml` (af00023); picked on VAL in `results/sim_choice_support.json` (91b09c0, kind cold starts) | VAL pick under the idle budget (the budget binds for support). It is not the PRD's 0.6. |
| lambda 100 | Cost threshold of the coding profile (pod-seconds worth paying per second of cold start saved) | `configs/model/profiles.yaml` (af00023); `results/sim_choice_coding_cap2.json` (fd3441e) | VAL pick. It is the end of the sweep, so it is not a well-located optimum. |
| 1 copy per tool | Shipped coding profile (`max_copies_per_tool: 1`) | `configs/model/profiles.yaml` (af00023); `results/sim_choice_coding_cap2.json` (fd3441e) | The one retune under the scale-up cap, picked on VAL. Adopted after the TEST margins were seen (`docs/limits.md`). |
| 30 s TTL | Default TTL of an unused warm pod | PRD FR-3, line 16; infra `charts/skypiea/values.yaml` `defaultTtlS: 30` (95d2d11); sim `reactive_ttl_ms` (`configs/model/sim.yaml`, d84c894) | The PRD default. It is also the keep-alive 30 s baseline and the coding profile's keep-after-use. |
| TTL + 5 s | Pod removed within TTL + 5 s of last use | PRD FR-3, line 17; infra `values.yaml` `purgeS: 5` (95d2d11); sim `purge_ms` (d84c894) | The FR-3 acceptance bound. The sim checks it with a 1 s tick. |
| 150 ms hold | Gateway holds a call up to 150 ms for a pod that is already starting | PRD FR-9, lines 25-27; infra `gateway/main.py` `SKYPIEA_HOLD_MS` default 150 (71fd418) | Avoids a second cold start racing the first. The offline simulator does **not** model the hold. |
| 5 s keep-after-use | `keep_after_use_s: 5` in `configs/model/decide.yaml` | `configs/model/decide.yaml` (1a72c23); VAL pick of the single-policy pass, `results/sim_choice.json` (baaabda) | Superseded: no shipped profile uses it (coding 30 s, support 0 s, prd 0 s). It is only the no-profile default. Do not quote it as the shipped value. |
| cap of 2 | At most 2 pods starting at once per node (`maxScaleUpsPerNode`) | infra `charts/skypiea/values.yaml` (95d2d11), introduced in b652e6b; rationale in infra `docs/gateway_controller.md` "Why at most 2 scale-ups at once" (ac311d9) | **A setting, not a measurement**: it equals the node's 2 vCPUs. E0 measured 1 and 3 starts at once, never 2. |
| 500 sessions | Concurrent sessions in NFR-1, NFR-4 and Scenario C | PRD NFR-1 line 28, NFR-4 line 34, Scenario C (`docs/prd_excerpts.md` "Scenario C") | The scale target. The offline sim does not run 500 sessions on one shared pool (`docs/limits.md`). |
| 30% idle budget | Tuning constraint: idle pod-seconds <= 30% of always-warm's, on every source | `configs/model/sim.yaml` `nfr3_idle_ratio: 0.3` (d84c894); `sim/tune.py` docstring | **Our interpretation** of PRD NFR-3 (line 32), which says CPU-seconds on pods *never used* <= 30% of always-warm's *total* CPU-seconds. The PRD's own ratio is also in every sim row (`nfr3_unused_over_always_warm`). |
| 3 pods per server, 20 in total | Operator caps in `apply_caps` | `configs/model/decide.yaml` `max_pods_per_tool`, `max_total_warm_pods` (1a72c23) | **UNSOURCED** design choice. 3 matches the controller's per-tool limit; 20 has no measurement behind it. |
| 5 new pods/s | Ramp limit for Scenario C | `configs/model/decide.yaml` `max_new_pods_per_s` (1a72c23) | **UNSOURCED** design choice. |
| 1% | NFR-4: missed-prediction rate <= 1% | PRD NFR-4, line 35 | Not measured by the model branch. |
| 60 s | NFR-6: session data purged within 60 s of session end | PRD NFR-6, lines 36-37 | Checked by `tools_audit/audit_no_persistence.py` (the service keeps no session state on disk). |

## Cold starts

| Number | What it is | Source | Why |
|---|---|---|---|
| about 2.7 s cached, 5.4 s uncached | AWS cold start of one pod alone, median over tools (single tool e.g. edit_file 2,717 / 4,452 ms) | infra `results/e0_aws_summary.csv` (2aea6da; E0 run at a24e540, n=10 per tool and mode); infra `docs/gateway_controller.md` table (ac311d9) | What prediction has to hide. Measured on one m7i-flex.large (2 vCPU) k3s node, with images pulled from ECR in the same region. |
| about 5.5 s cached, 9.0 s uncached | AWS cold start when 3 pods start at once (median) | infra `results/e0_aws_p3_summary.csv` (2aea6da); `docs/gateway_controller.md` (ac311d9) | Starting three at once roughly doubles each start (CPU-bound app_init on 2 vCPUs). The sim uses this for any overlap, including 2 at once. |
| sha256 00758310b4d6 | The AWS cold-start file used by every AWS result and by all three bundles | infra `configs/cold_start.yaml` (2aea6da) | Ties each result row to the cold starts it used (`cold_start_sha256` column). |
| sha256 96d8a3d6952d | The kind cold-start file (lower bound) | infra `configs/cold_start.yaml` at c21a567 | Comparison runs only. |

## Hit rates and margins (TEST; `docs/results_summary.md`)

| Number | What it is | Source | Why |
|---|---|---|---|
| support +15.7 / +18.2 points | Equal-idle margin over keep-alive on tau2, cached / uncached, AWS cap 2 + parallel_3 | `results/sim_sensitivity.csv.gz` (64e65c9) | The strong claim: support beats keep-alive at the same idle in every run (+15.6 to +18.5). |
| support 61.4% warm at 11,859 idle pod-s / 1k | tau2, cached, AWS + cap (keep-alive 30 s: 56.4% at 18,218) | `results/sim_sensitivity.csv.gz` (64e65c9) | More warm calls with about 35% less idle than keep-alive 30 s. |
| support FR-2 86.8% / 90.9% | Share of predicted calls Ready on arrival, at tau 0.4 (its own tau), tau2, cached / uncached | `results/sim_offline.csv.gz` (aabdfd2) | Above the 80% bar, but at tau 0.4 rather than 0.6, and on 76 / 44 predicted calls. |
| coding (shipped) +3.5 / +3.4 TraceLab, +5.1 / +5.0 Copilot | Equal-idle margin over keep-alive, AWS cap 2 + parallel_3 | `results/sim_sensitivity.csv.gz` (64e65c9) | The modest claim. The frozen coding profile was -1.9 / -2.4 on TraceLab. |
| ranker top-1 0.529 [0.48, 0.57] TraceLab vs n-gram 0.454 | E1 next-tool top-1, fixed split, 95% group-bootstrap CI | `results/p1_accuracy.csv.gz` (52fdd3e) | The ranker beats the n-gram baselines on TraceLab and Copilot, but not on tau2 (0.520 vs 0.620). |
| oracle 91.1 / 79.7 / 78.1% | Uncapped ceiling (AWS, no cap, no contention), cached | `results/sim_sensitivity.csv.gz` (64e65c9) | Headroom with perfect knowledge. Not achievable under the cap (54.0 / 59.7 / 72.5%). |
| hideable 91.1 / 79.1 / 78.1% cached, 67.8 / 56.0 / 56.1% uncached | Share of calls whose think gap covers the AWS cold start (TraceLab / Copilot / tau2) | `results/sim_hideable.csv` (aabdfd2) | The most any prediction could hide just in time. |
| prd 42.9% warm | SYNTHETIC prd profile on synthetic TEST, cached | `results/sim_prd.csv.gz` (64e65c9) | Shows the mechanism runs on the PRD scenarios. It is **not** evidence about real workloads. |
| 28 VAL / 56 TEST sessions | tau2 sessions with timing | `data/processed/tau2_bench/splits.json`, from the pipeline (not committed; rebuilt with `python -m traces.pipeline`) | Small. A few sessions move the tau2 numbers by several points. The plan's "n=15" does not match the data. |
