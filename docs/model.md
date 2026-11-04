# Prediction engine (branch feat/model)

Work in progress; the full write-up is Phase 12. This file holds the calibration and cap
decisions, the warm-decision rules (Phase 5), the offline-simulation definitions (Phase 6) and
the predictor service contract alignment.

## Calibration (decided 2026-11-03)

The ranker ships **raw LightGBM scores** (`configs/model/ranker.yaml`, `calibration: none`).
`calibration: isotonic` applies the VAL-fitted isotonic map instead; the map is always fitted
and saved (`calibrator.json`) so either mode can be served. `python -m predictors.phase4`
reports TEST-set ECE both ways from one fitted model (`results/p4_report.md`). VAL cannot
choose between them: the isotonic map is fitted on VAL, so its VAL ECE is in-sample (0.000).
On TEST, raw row ECE is .002-.004 on the real sources and isotonic makes it slightly worse
there; isotonic helps only on tau2 and synthetic A. Calibration never reorders candidates, so
top-1/top-3 are identical in both modes.

## Per-session cap (decided 2026-11-03)

`per_session_cap` stays at **3** (`configs/model/graph.yaml`). Measured on full TraceLab
native, fixed split and 5-fold GKF (`python -m predictors.cap_sweep`, `results/p4_cap.md`):
cap 3 vs no cap costs at most 0.9 pt top-1 (graph n-gram, fixed split only; 0.0 under GKF;
ranker 0.1 pt), below the 1-point rule.

## Warm decisions (Phase 5, `predictors/decide.py`)

Parameters: `configs/model/decide.yaml`; cold starts: the infra cold-start file, loaded by path
(`predictors/cold_start.py`; no cold-start number lives in code; tool classes map to servers
by the file's `classes` lists, unlisted classes to its `fallback`). Prices and per-tier CPU:
`configs/model/pricing.yaml` (assumptions).

- Probabilities: the fail-open ladder (`predictors/ladder.py`) ranker -> n-gram -> majority
  -> none; a rung that raises, returns invalid values or exceeds the 40 ms budget (checked on
  return) is skipped and `degraded=true`.
- Abstain (`mode="prior"`, no decisions): last tool unseen in train, or max in-next-batch
  probability < `min_conf` (0.3). All rungs failing: `mode="none"`. Both mean the cold path.
- Threshold: fixed `tau`, or cost-based (`threshold: cost`) with the policy knob
  `lambda_pod_s` = pod-seconds worth paying to save one second of cold start: warm j iff
  p_j >= (cold_s + ttl_s) / (cold_s + ttl_s + lambda x cold_s), clamped to [0.05, 0.99]
  (a wrong warm wastes the startup plus the hold; a right one saves lambda x cold_s). No
  single value of a second of latency is assumed; lambda is swept (1, 3, 10, 30, 100) and the
  default is picked on VAL by the PRD rule below.
- Copies: smallest n with P(count <= n | tool in batch) >= `copies_quantile`, capped at
  min(`max_copies_per_tool`, 10).
- Start rule (`start_rule`): `timing` = start_in_ms = max(0, gap_<gap_stat> - cold_<plan_stat>
  - margin), TTL from Ready until the p90 arrival, clamped to [5 s, 600 s], 30 s without
  timing data (the spec rule is gap p50 and cold p95); `now` = start now, 30 s TTL.
- `keep_after_use_s` (contract P-new): after a pod serves a call, the controller keeps it for
  max(`ttl_s` of the latest decision naming its server, `keep_after_use_s`). Baselines keep
  30 s; our policy may set it small.
- Per server (contract P7): copies max, start_in min, ttl max. Operator caps
  (`apply_caps`): 3 pods per server, 20 in total, an optional ramp budget; when a cap binds,
  pods go to the highest prob x cold-start ms.

## Results in plain language (policy frozen 2026-11-04; AWS cold starts)

**What was built.** We built a next-tool ranker (LightGBM over a transition graph) and a warm policy.
The policy turns the ranker's probabilities into "start this many pods of this server, now or in x ms, and
keep them y s". It has two named profiles in `configs/model/profiles.yaml`. Each profile was
tuned only on its own sources' VAL split, under the PRD rule: maximise warm calls subject to
idle pod-seconds <= 30% of always-warm. Each was then evaluated once on TEST. One deployment serves one
profile (P1; see "Image: build, push, run"). Scenario A uses `support`, and Scenario B uses `coding`.

| Profile | Sources | Rule picked on VAL | NFR-3 budget |
|---|---|---|---|
| coding | TraceLab, Copilot | start now, copies q0.9, keep 30 s after use, cost threshold lambda = 100 pod-s per cold-start second | **not binding** (worst idle 5.9% of always-warm on VAL; 2-8% on TEST with AWS values); lambda 100 is the end of the sweep, so the rule simply buys as much warmth as the sweep allows |
| support | tau-bench, tau2 | start now, copies q0.9, keep 0 s after use, fixed tau 0.4 | binding (22% of always-warm on VAL, 23-24% on TEST; the best setting without the budget would give 76.6% warm on VAL instead of 69.6%) |

The tuning used the kind cold-start file, and the policy is frozen. Nothing was re-tuned on the AWS values.

tau-bench has no timestamps (0 of 1,928 sessions), so it cannot be replayed. The support
profile therefore rests on tau2 alone. tau2 has 28 VAL and 56 TEST sessions with timing (342
TEST calls); "n=15" in the plan does not match the data.

**Cold starts used.** The primary results use the AWS file (infra `2aea6da`, sha256
`00758310b4d6`). It was measured on an m7i-flex.large node (2 vCPU) running k3s, with images
from ECR in the same region. The sim follows the controller (infra `b652e6b`): at most **2 scale-ups at once** per
node, and the rest wait in a queue. A start that overlaps another takes the file's **parallel_3** time, the
measured time when 3 tools start at once (about 5.4 s cached instead of 2.7 s alone). Each
session is its own node in the sim. The kind file (infra `c21a567`, sha256 `96d8a3d6952d`, a
lower bound) is kept for comparison. With the cap and contention switched off, the new sim gives
the earlier kind numbers exactly (222 of 222 rows).

**Final table** (TEST, cached images; warm % of calls / idle pod-seconds per 1,000 calls; AWS
cold starts, scale-up cap 2, parallel_3 contention; `results/sim_report.md`):

| Policy | TraceLab (coding) | Copilot (coding) | tau2 (support) | tau-bench (support) |
|---|---|---|---|---|
| keep-alive 30 s | 56.9 / 17,622 | 49.6 / 19,301 | 56.4 / 18,218 | not replayable |
| keep-alive 60 s | 64.8 / 28,658 | 58.3 / 32,233 | 60.8 / 30,363 | not replayable |
| Markov-1 top-1 (tau 0.6) | 56.9 / 17,632 | 49.3 / 19,329 | 63.7 / 19,500 | not replayable |
| ours, old (one policy, tau 0.6) | 58.6 / 25,241 | 50.7 / 21,083 | 58.2 / 18,746 | not replayable |
| **ours, profile** | **70.8 / 53,587** | **64.8 / 45,556** | **61.4 / 11,859** | not replayable |
| oracle (just-in-time) | 54.0 / 551 | 59.7 / 4,661 | 72.5 / 532 | not replayable |
| always-warm (1 pod per server) | 87.8 / 2,375,346 | 91.0 / 665,196 | 100.0 / 50,923 | not replayable |

**Equal-idle comparison with keep-alive.** The margin is our warm % minus keep-alive's at the
same idle, interpolated log-linearly between keep-alive 5 s and 600 s
(`results/sim_kind_vs_aws.md`). Each cell gives the shipped point, cached / uncached:

| Source | Profile | kind | AWS, no cap | **AWS, cap 2 + parallel_3** | sweep points above keep-alive (AWS, cap) |
|---|---|---|---|---|---|
| TraceLab | coding | +1.2 / +2.2 | +1.5 / +2.4 | **-1.9 / -2.4** | 0 of 20 |
| Copilot | coding | +5.2 / +5.7 | +5.4 / +5.7 | **+2.4 / +1.9** | 4 of 20 |
| tau2 | support | +18.5 / +16.1 | +18.1 / +17.7 | **+15.7 / +18.2** | 17-18 of 18-19 |

**What changed from kind to AWS.**
- **The AWS numbers alone change little.** Cached cold starts are within -0.3 / +0.7 s of kind
  (2.6-3.8 s). Uncached ones are 0.6-2.5 s slower (4.3-8.4 s). Without the cap, cached warm % moves
  by under 1 point (the oracle by about 3). Uncached warm % moves by up to 3 points (the oracle
  by 11-15). The margins in the "AWS, no cap" column are within 1.6 points of kind.
- **Contention and the cap cause the changes.** Our coding profile starts many pods at
  once (up to 3 copies of several servers). On TraceLab, 55% of its starts wait for a slot (mean
  7.8 s cached) and 83% overlap another start. For keep-alive 30 s the figures are 16% and 40%.
  Its warm % drops 5 points (75.3 -> 70.8 cached), and keep-alive drops only 1.6.
- **Conclusion that changed: TraceLab.** The coding profile is now *below* keep-alive at equal
  idle (-1.9 / -2.4), and no point of its sweep is above it. On TraceLab, prediction does not
  pay on a 2-vCPU node.
- **Conclusion that weakened: Copilot.** The margin halves (+5.2 -> +2.4 cached, +5.7 -> +1.9 uncached).
- **Conclusion that held: tau2.** The support profile starts few pods (tau 0.4, keep 0 s), so it
  rarely queues (0-2% of starts). It stays 16-18 points above keep-alive at equal idle, with 35%
  less idle than keep-alive 30 s.
- **Hideable calls drop with AWS uncached starts.** The share of calls whose think gap covers the cold start falls from 82 to 68%
  on TraceLab, 68 to 56% on Copilot, and 68 to 56% on tau2. Cached it is about the same: 94 -> 91, 82 -> 79,
  78 -> 78 (`results/sim_report.md`, last table).
- **The oracle is no longer a useful ceiling under the cap.** It starts each pod at the last
  moment, so any queueing or contention makes it late (TraceLab 93.9 -> 54.0% warm cached).
  It does not know about the cap. Its idle stays tiny.

Wait per call: `results/sim_added_cdf.png` shows the CDF of each call's wait for a pod, with AWS + cap
solid and kind dashed. Pareto plots: `results/sim_pareto.png` (AWS + cap) and
`results/sim_pareto_kind.png`.

**What we claim.**
- **Support profile on tau2:** it warms 61% of calls (cached) with 35% less idle than keep-alive 30 s
  (56%). At equal idle it is 16-18.5 points above keep-alive, on kind and on AWS, with or without
  the scale-up cap. Markov-1 top-1 is also well above keep-alive on tau2, so much of this is
  "the next tool is predictable", not the ranker in particular.
- **Coding profile on Copilot:** with AWS cold starts and the cap it is 2 points above keep-alive at equal idle
  (65% warm at 46k idle pod-s per 1,000 calls).
- **Coding profile on TraceLab:** with AWS cold starts and the cap it does not beat keep-alive; it buys more warm
  calls only by spending more idle, as keep-alive 60 s does.
- **Where prediction pays off:** see the cold-call decomposition below (analysis only; nothing was tuned on it).

  Calls left cold by keep-alive 30 s, by cause, and the share our profile warms (AWS + cap,
  cached; `results/sim_cold_decomp.md`; kind values in brackets):

  | Source | first use of the server | gap > 30 s | other (busy pod, parallel calls) | idle /1k: KA30 -> ours |
  |---|---|---|---|---|
  | TraceLab | 4.8% of calls, 8.1% warmed (21.7) | 31.2%, 35.2% (39.4) | 7.1%, 46.9% (64.8) | 17,622 -> 53,587 |
  | Copilot | 9.2%, 21.3% (30.5) | 32.8%, 36.8% (42.3) | 8.3%, 21.8% (22.4) | 19,301 -> 45,556 |
  | tau2 | 38.3%, 17.6% (24.4) | 3.8%, 0.0% (0.0) | 1.5%, 40.0% (50.0) | 18,218 -> 11,859 |

  On coding traces most keep-alive misses are long gaps. Our profile recovers about 35% of
  them (about 40% on kind) by spending about 2.4-3x the idle. On tau2 most misses are first uses
  of a server, which no keep-alive can warm. Prediction warms 18% of them (24% on kind) while using
  less idle. With the cap, every share is lower, because warming starts queue behind each other.

**What we do not claim.**
- **tau-bench:** no result, because it has no timestamps.
- **Production latency or cost:** no numbers. The pricing is an assumption (configs/model/pricing.yaml), and
  the cold starts come from one 2-vCPU node.
- **The ranker as the reason tau2 works:** not claimed. Markov-1 top-1 is already +6.7 to +9.6 points above
  keep-alive at equal idle there, in every run.
- **Coding traces:** not that prediction helps there in general. On a 2-vCPU node with the cap it does not
  on TraceLab, and it helps only a little on Copilot.

**Limits.**
- **Per-session pods, no shared pool.** Pods are per (session, server); nothing is shared across sessions.
  Real deployments share one Deployment per tool server, which would help keep-alive and
  always-warm (and Scenario C, 500 sessions on one tool) more than shown.
- **The scale-up model is approximate.** Each session is its own node, so other sessions never compete for
  the 2 slots, and real contention is higher. The parallel_3 time (3 at once) is used for 2 at once
  because 2 was not measured, which overstates contention. A start already running is not slowed by a later
  one, which understates it. The queue is first come, first served.
- **tau2 is small:** 28 VAL / 56 TEST sessions with timing, 342 test calls. The numbers move by several points
  with a few sessions.
- **tau-bench cannot be replayed** (no timestamps).
- **The coding profile has no brake on idle.** Its budget does not bind and its lambda sits at the end of the sweep. On
  those sources the PRD rule alone does not stop it spending more idle, and with the cap that
  extra spending also queues its own starts.
- **The policy was tuned on kind values.** It was not re-tuned for AWS or the cap, so a coding profile tuned with
  the cap might start fewer pods.
- **Tuning disclosures:** one TEST look at tau2 (2026-11-03, disclosed below); VAL also served the ranker's
  early stopping.
- **No fresh-eyes review:** the Phase 11 review was skipped for time.

**Oracle headroom.** On kind cold starts, the just-in-time oracle warms every call whose
think gap is at least the server's cold start, at almost no idle (568 / 4,716 / 511 pod-s per
1,000 calls). That is 4-36x less idle than keep-alive 30 s, while it warms more calls than
our profiles (93.9 / 82.6 / 77.5% vs 75.3 / 69.4 / 64.0%). It is not an upper bound on
warm %, because keeping pods also warms short-gap calls. Under the AWS cap the oracle as
written is late (its starts queue), so the headroom it shows there is idle only. A
cap-aware oracle that starts earlier when slots are busy was not built.

**Latency** (`services/predictor/loadtest.py`, results/loadtest.csv; service and load client on
the same laptop; coding bundle lgbm-coding-69d7b94 (same model files as the current coding
bundle); requests = 500 TraceLab VAL decision
points, histories up to 200 calls):

| Load | Workers | req/s served | p50 ms | p99 ms | errors | service compute p99 ms |
|---|---|---|---|---|---|---|
| open loop 75 req/s (~500 sessions), 60 s | 1 | 73.8 | 6.5 | 13.3 | 0 | 5.5 |
| open loop 150 req/s, 60 s | 1 | 149.6 | 5.8 | 15.9 | 0 | 5.6 |
| open loop 75 req/s, 60 s | 2 | 73.8 | 47.7 | 56.3 | 0 | 5.1 |
| open loop 150 req/s, 60 s | 2 | 149.6 | 48.0 | 57.1 | 0 | 4.4 |
| closed loop 10 clients, 30 s | 2 | 196.9 | 50.8 | 62.3 | 0 | 5.8 |
| closed loop 10 clients, 10 s | 1 | 892.8 | 10.0 | 37.6 | 0 | 7.5 |
| closed loop 200 clients, 60 s | 1 | 273.0 | 395.9 | 5,122 | 1 | 10.7 |
| closed loop 200 clients, 60 s | 2 | 206.6 | 620.7 | 5,226 | 0 | 6.6 |

One worker meets p99 < 50 ms at 75 and 150 req/s open loop. Two workers add a constant ~45 ms
per request that I did not find the cause of (not LightGBM threads: OMP_NUM_THREADS=1 did not
change it); use **one worker per pod and scale with replicas** (this is also what /reload
needs). The 200-client closed loop is queueing: 200 clients each waiting on their previous
answer saturate one process (and the Python client on the same machine), so latency is the
queue length, not the service time (compute p99 7-11 ms); it is kept only as a stress number.
RSS with one worker stayed at about 245-266 MB (with two workers the tool measured the
supervisor process, so no memory number for that case).

## Offline simulation (Phase 6, `sim/offline.py`)

Definitions are in the module docstring; the key modelling choices are: pods are per (session,
server), never shared across sessions; decisions are made at the end of each batch (no
request before the first call); a call that finds its pod still starting waits for it (FR-9
hold); after a use the pod is kept for max(latest decision ttl for its server,
`keep_after_use_s`) (P-new; the old policy used the decision TTL until first use, then 30 s); "hideable" = not in the first batch and think gap >= the server's
median cold start.

First pass (2026-11-03, commit 7a684c0): on TraceLab and Copilot the policy (tau 0.6,
copies q0.9, spec timing rule, 30 s after use) did not beat fixed keep-alive; prewarm
precision 7.5% on TraceLab.

### Second pass: why prewarms go unused, tuned on VAL (2026-11-04)

Runner `sim/tune.py`; every variant x threshold on VAL (cached images) is in
`results/sim_variants.csv.gz` (winners and losers), the steps and scores in
`results/sim_choice.json`. Objective (PRD NFR-3): over the thresholds (tau 0.3..0.9, lambda
1, 3, 10, 30, 100) maximise the mean warm % over the three sources subject to idle
pod-seconds <= 30% of always-warm **on every source**. tau2 binds: keep-alive 30 s alone
uses 36% of always-warm there, so with a 30 s keep after use no variant is feasible.

| Step (VAL) | Variants | Best feasible: mean warm % (worst idle ratio) | Pick |
|---|---|---|---|
| a copies (30 s keep) | q0.9 / q0.5 / one | none feasible; all 53.2 (0.379) | tie -> q0.9 (kept old) |
| b start (30 s keep) | spec / jit_p50 / jit_p10 / now | none feasible; all 53.2 (0.379) | tie -> spec |
| c keep-after-use | contract rule 0/2/5/10/30 s: 61.2 / 61.2 / 61.3 / 60.3 / infeasible; hold-only 0/2/5/10 s: 56.4 / 57.8 / 58.8 / 60.4 | 61.3 (0.299) | 5 s, contract rule |
| a2 copies (5 s keep) | q0.9 61.3 / q0.5 58.9 / one 58.9 | 61.3 | q0.9 |
| b2 start (5 s keep) | spec 61.3 / jit_p50 61.3 / jit_p10 61.9 / now 61.2 | 61.9 (0.211) | jit_p10 |
| timing (item 2) | spec 61.26 / now 61.15 / jit_p10 61.87 | 61.9 | jit_p10 |
| threshold | best feasible setting of the winner | tau 0.3 | tau 0.3 (fixed) |

Shipped at the time (configs/model/decide.yaml; **superseded by the profiles above**): copies q0.9, start at the think gap's p10 minus the
median cold start (TTL until the p90 arrival), `keep_after_use_s` 5, fixed tau 0.3. Item 2:
between the two rules asked about, the spec rule beat "start now, 30 s TTL" on VAL by 0.1
point; the shipped rule is a variant of the timing rule (p10 gap, median cold start). The
NFR-3 rule picks a lower tau than FR-2's 0.6; FR-2 is still reported at the shipped tau.
lambda 100 (the end of the sweep) won steps c and a2; the final pick is a fixed tau.

Findings on the unused prewarms (TEST, cached; `results/sim_report.md`):
- (a) copies: fewer copies saves little idle but loses more warm calls (VAL), so q0.9 stays.
  Surplus copies are 51% of unused prewarms on TraceLab for the old policy, 34% for the new.
- (b) start time: just-in-time at the p50 changes nothing; planning to the p10 gap helps.
- (c) replacing the 30 s keep with 5 s is what makes the policy feasible under NFR-3.
- (d) 72-82% of our per-server warm decisions (TraceLab, Copilot; old and shipped) name a server that already
  has enough live pods, so they start nothing (they only extend a pod). Calls "about to be
  made by another session" cannot occur in this sim: pods are per session (see below).
- Prewarm precision rises from 7.5% to 16.9% on TraceLab (old tau 0.6 vs shipped).

Results on TEST (cold starts: E0 kind file, a lower bound; idle pod-s per 1,000 calls):

| Policy | TraceLab warm % | idle | Copilot warm % | idle | tau2 warm % | idle |
|---|---|---|---|---|---|---|
| keep-alive 30 s | 58.5 | 18,090 | 51.4 | 19,476 | 56.1 | 18,299 |
| keep-alive 60 s | 66.3 | 29,220 | 60.1 | 32,478 | 60.5 | 30,531 |
| Markov-1 top-1 | 58.5 | 18,100 | 51.1 | 19,502 | 66.4 | 19,742 |
| ours, old (tau 0.6) | 60.5 | 25,804 | 53.1 | 21,280 | 58.8 | 18,758 |
| ours, shipped | 69.0 | 62,874 | 68.2 | 77,920 | 46.8 | 11,031 |
| oracle | 93.9 | 568 | 82.6 | 4,716 | 77.5 | 511 |

**Against keep-alive** (warm % at the same idle, log-linear between keep-alive 5 s ... 600 s):
- TraceLab: **no point of ours beats the keep-alive curve** (best -1.9 points; shipped
  default -6.8). The predictive policy is not worth running on TraceLab-like traffic.
- Copilot: 3 of 20 points beat it (best +5.5, lambda 100); the shipped default is -2.2.
- tau2: 18 of 20 points beat it (best +7.2); the shipped default +3.6. Markov-1 top-1 (66.4%
  at 19.7k) beats both our policy and keep-alive on tau2.
The shipped default meets NFR-3 by a wide margin on TraceLab and Copilot (idle 3% / 12% of
always-warm) because the tau2 constraint drove the choice; it spends idle where keep-alive
spends it more efficiently on those two sources. A per-source default would do better; we
did not tune one.

**Disclosure.** While finding the first sim bug (2026-11-03) I looked at the tau2 TEST
split once (timing rule vs start-now). No choice was made from it, and the test split was not
used for any choice in this pass; all choices above are from VAL. The ranker uses VAL for
early stopping and the n-gram weights, so VAL numbers are slightly optimistic for it.

**Not modelled (per-session mode is the conservative case).** Pods are per (session,
server); real deployments share one Deployment per tool server across sessions, which would
help keep-alive and always-warm more than it is shown here and makes Scenario C (500
sessions on one tool) meaningful only with sharing. The shared-pool ablation (Poisson session
arrivals, concurrency 1/10/100/500, copies from fleet demand) was not built in this pass. The
operator caps (20 pods in total, ramp limit) are applied per session in the sim, so they
almost never bind.

## Predictor service (Phase 9, `services/predictor/`, built 2026-11-04)

Implements the contract as decided (v1 + P2, P3, P6, P7, P8, P-new): `POST /decide` reads
`history[]` (last 200 used, `truncated`; > 1,000 items or a body > 256 KB -> 413), accepts
`history_total`, ignores `oracle`; answers `ranked` (top 5, sum <= 1, `END`), `decisions`
(warm entries plus up to 3 `warm=false, copies=0` candidates), `keep_after_use_s`,
`model_version`, `mode`, `degraded`, `truncated`, `latency_ms`. HTTP 200 for any JSON body
under the limits (bad session id, bad fields or a model failure -> no decisions, degraded);
400 for malformed JSON. Inputs are repaired per `services/predictor/schema.py` and counted by
field name only. `/reload` (admin token, constant-time compare; MODEL_ROOT paths or
allowlisted s3:// only; hash, schema, vocabulary, golden-answer and 200-call warm-up p99 <= 50
ms checks before an atomic swap), `/rollback`, `/healthz`, `/metrics`. Bundles:
`models/README.md`. Dockerfile: same pinned base image as the platform image, non-root, no
shells (built; see below).

Changes 2026-11-04: the ranker scores once per request (one booster call serves `ranked` and
the decisions; golden answers unchanged); `WORKERS` uvicorn processes (with more than one,
/reload and /rollback answer 409, because each worker holds its own model: change models by
rolling the pods); training-only packages (scikit-learn, pandas, pyarrow) are not imported at
serving time.

Load test: see "Latency" in the results section above.

### Image: build, push, run (for the infra window)

**One predictor per deployment (P1, decided 2026-11-04).** There is no scenario field in the
request. A deployment serves one profile, chosen by the chart value `predictor.profile`
(`coding` | `support` | `prd`). The Scenario A demo uses `support`, and Scenario B uses `coding`;
`prd` is the **SYNTHETIC** demo bundle for both PRD scenarios (see "Synthetic PRD demo profile"
below). One image holds the three bundles, at `/models/coding`, `/models/support` and
`/models/prd`. The chart passes the value as the
env var `PREDICTOR_PROFILE`, and the service then serves `MODEL_ROOT/<profile>`. A bundle
whose manifest names another profile is refused at startup (`/healthz` stays 503). To serve
a bundle from a mounted volume instead, set `MODEL_PATH` to its folder (this overrides the
profile's default path; keep it under `MODEL_ROOT`).

    # feat/model worktree, clean commit; one bundle per profile (not in git)
    COLD=~/skypiea-infra/configs/cold_start.yaml     # the file the pods will mount (AWS now)
    nice -n 10 .venv/bin/python -m services.predictor.export --source tracelab --profile coding \
        --cold-start $COLD                           # -> models/lgbm-coding-<commit>/
    nice -n 10 .venv/bin/python -m services.predictor.export --source tau2_bench --profile support \
        --cold-start $COLD                           # -> models/lgbm-support-<commit>/
    nice -n 10 .venv/bin/python -m traces.generate_synthetic --type prd   # SYNTHETIC traces
    nice -n 10 .venv/bin/python -m services.predictor.export --source synthetic_prd --profile prd \
        --cold-start $COLD                           # -> models/lgbm-prd-<commit>/
    TAG=$(git rev-parse --short=12 HEAD)
    docker build -f services/predictor/Dockerfile \
        --build-arg CODING_DIR=models/lgbm-coding-<commit> \
        --build-arg SUPPORT_DIR=models/lgbm-support-<commit> \
        --build-arg PRD_DIR=models/lgbm-prd-<commit> -t skypiea/predictor:$TAG .
    # (or: make -f mk/model.mk predictor-image)
    # into the kind registry, then ECR (Window 1, feat/infra):
    docker tag skypiea/predictor:$TAG localhost:5001/skypiea/predictor:$TAG
    docker push localhost:5001/skypiea/predictor:$TAG
    python infra/aws/scripts/push_ecr.py --registry <account>.dkr.ecr.ap-south-1.amazonaws.com \
        --tag $TAG --images predictor          # needs the ECR repository skypiea/predictor

Chart (feat/infra owns it; suggested shape):

    predictor:
      profile: support            # Scenario A; coding for Scenario B; prd = SYNTHETIC demo
    # Deployment env: PREDICTOR_PROFILE={{ .Values.predictor.profile }},
    #   COLD_START_PATH=/cold/cold_start.yaml (ConfigMap of configs/cold_start.yaml)

**Cold-start file and bundles.** A bundle records the sha256 of the cold-start file it was
exported with (`cold_start_sha256` in MANIFEST.json). Its golden answers include the
decisions, and those depend on cold starts. When the mounted file is the same, the full golden
check runs. When it is a different file (for example kind instead of AWS), only the ranking is
checked, the model still loads, and `/healthz` shows `"cold_start_matches_bundle": false`.
Export with the file the pods mount (AWS now).

Run: container port 8090; read-only root filesystem works; user 10001; no shell in the image.

| Env | Value |
|---|---|
| `PREDICTOR_PROFILE` | `coding` (image default), `support` or `prd` (SYNTHETIC demo), from the chart value `predictor.profile` |
| `COLD_START_PATH` | mounted cold-start file, e.g. a ConfigMap of configs/cold_start.yaml at `/cold/cold_start.yaml` (required) |
| `MODEL_PATH` | unset (then `MODEL_ROOT/<profile>`); set it only to serve a mounted bundle folder |
| `MODEL_ROOT` | `/models` (default); reloads only from folders under it |
| `ADMIN_TOKEN` | from a Secret; unset disables /reload and /rollback |
| `WORKERS` | 1. Two workers added ~45 ms per request in the load test, cause unknown. Run one worker per pod and scale with replicas; /reload also needs 1. |
| `PORT` | 8090 (default) |

Probes: readiness and liveness `GET /healthz` (503 until a model is loaded; the body names
`model_version` and `profile`). Metrics: `GET /metrics` (Prometheus; annotate the pod with
the metrics port). The gateway points `platform.predictor.url` at `http://<service>:8090`. The
request/response is the contract plus `keep_after_use_s`, `mode`, `degraded` and `truncated`.

## Predictor service contract: alignment with docs/predictor_contract.md (draft v1)

The gateway codes against `docs/predictor_contract.md` (infra branch, commit 18a711f) and its
validator `gateway/contract.py`. The predictor service (Phase 9, not written yet) will conform
to it exactly: `POST /decide {session_id, now_ms, history[...]} -> {ranked, decisions,
model_version, latency_ms}`. The table lists every field where the original model-side design
(Phase 9 spec: `POST /predict`) differs, and what the service will do.

| # | Original model-side design | Contract v1 | Service will |
|---|---|---|---|
| 1 | `POST /predict` | `POST /decide` | serve `/decide` |
| 2 | request `events[]` | `history[]` | read `history` |
| 3 | event `tool` | `tool_class` | read `tool_class` |
| 4 | event `t_offset_ms` (when the LLM emitted the batch) | `t_start_ms` | use `t_start_ms` as `t_offset_ms` |
| 5 | event `service_ms` | `t_end_ms` | derive `service_ms = t_end_ms - t_start_ms` (clipped at 0) |
| 6 | (none) | `now_ms` | accept; validate as finite and >= 0; not used as a feature in v1 (see P5) |
| 7 | request `scenario` (selects the model) | absent | one model per deployment, chosen at startup (see P1) |
| 8 | request `node` (workflow node, optional) | absent | node = null, so learned edges only; real traces have no nodes anyway (see P1) |
| 9 | (none) | `oracle[]` (optional) | accept and ignore it |
| 10 | `session_id` must match `^[A-Za-z0-9_.-]{1,64}$` | opaque `Mcp-Session-Id` | see P2 |
| 11 | more than 1,000 events -> 413; more than 200 -> use the last 200, `truncated=true` | "all calls of this session so far", no limit | see P3 |
| 12 | ranked item `{tool, prob}` | `{tool_class, p}` | emit `{tool_class, p}` |
| 13 | end token `<end>` | `END` | map `<end>` -> `END` at the boundary |
| 14 | decision `{tool, copies, start_in_ms, ttl_ms, prob}` | `{tool_class, warm, copies, start_in_ms, ttl_s, prob}` | emit contract fields |
| 15 | (no `warm` flag; only positive decisions) | `warm: bool`; `false` entries allowed | emit `warm=true` for decisions; below-threshold candidates as `warm=false, copies=0` (logged by the gateway) |
| 16 | `copies >= 1`, capped by `max_copies_per_tool` | int in [0, 10] | cap at min(config cap, 10) |
| 17 | `start_in_ms` float | int >= 0 | round to int, clip at 0 |
| 18 | `ttl_ms` in [5,000, 600,000] | `ttl_s` int >= 0 or null (= controller default 30 s) | `ttl_s = ceil(ttl_ms / 1000)` (5..600); never null (see P4) |
| 19 | response `mode`, `degraded`, `truncated` | absent (the validator ignores unknown keys) | send them as extra fields; see P6 |
| 20 | abstain = `mode="prior"` + no decisions | not described | `decisions: []` (valid under the contract) |
| 21 | always HTTP 200 for well-formed JSON; 413 for oversize bodies | status codes not specified; gateway timeout 300 ms | keep this behaviour; the internal budget of 40 ms is well under 300 ms |
| 22 | `/reload`, `/rollback`, `/healthz`, `/metrics` | not in the contract | add them; they do not affect `/decide` |

### Proposed changes to the contract (for the infra owner; not applied)

- **P1. Scenario selection.** The contract has no `scenario`, so one predictor serves one
  scenario. Proposal: add an optional `scenario` string (`coding` | `support`). The service
  answers with `decisions: []` if no model is loaded for it. Alternatively, state in the
  contract that the chart deploys one predictor per scenario. An optional `node` would only
  matter for synthetic workflow traces; I do not propose adding it.
- **P2. `session_id` format.** The service is stateless and never needs the raw id, and an
  MCP session id can be any visible-ASCII string of any length. Proposal: the gateway sends
  an opaque hash (for example the first 32 hex characters of the id's sha256), and the contract
  fixes the format to `^[A-Za-z0-9_.-]{1,64}$`. Until then the service accepts any string up
  to 256 characters, never logs it, and rejects longer ones with 413.
- **P3. History length.** TraceLab sessions reach thousands of calls; the model reads only the
  last 200 events. Sending the full history on every call is O(n^2) bytes per session.
  Proposal: the gateway sends at most the last 200 calls, plus `history_total: int` (the
  number of calls so far, used for the `step` feature). Without this, the service accepts up
  to 5,000 items, uses the last 200, and sets `truncated: true`. A 256 KB body limit holds
  about 2,000 items, so the limit would be raised to 1 MB.
- **P4. TTL unit.** `ttl_s` as an integer loses sub-second precision. That is harmless, since
  the minimum TTL is 5 s. `null` means the controller's default of 30 s. The service always
  sends an explicit value so the logged TTL is the one used.
- **P5. `now_ms`.** The model's timing features are trained on the gap between batches.
  `now_ms` equals the end of the latest call, so it adds nothing over `t_end_ms`. The contract
  is fine as is; the service only validates it.
- **P6. Optional response fields.** Proposal: document `mode` (`ranker` | `ngram` |
  `majority` | `prior` | `none`), `degraded` (bool) and `truncated` (bool) as optional
  response fields. That lets the gateway count fallbacks without the service changing shape.
- **P7. Class vs server granularity.** Decisions are per tool class, but pods are per server;
  for example, `shell` serves six classes. Two classes on one server could both ask for warm
  pods. Proposal: the contract states that the controller merges decisions per server, taking
  the max of `copies`, the min of `start_in_ms` and the max of `ttl_s`. The predictor does not
  read `tools/tool_map.yaml`.
- **P8. `batch_id` semantics.** The contract defines a batch as calls that *overlapped in
  time* at the gateway. The training data defines it as calls the LLM *emitted together*.
  These usually agree, but a slow call overlapping the next batch's first call would merge two
  batches. Proposal: the gateway assigns batch ids by the client request that carried the
  calls when possible, and by overlap only as a fallback. The contract should say which one
  applies.

### Decisions on the proposals (user, 2026-11-03)

This list goes to the infra session as written; the infra window applies it to
`docs/predictor_contract.md` and the gateway. The service (Phase 9) implements the decided
form.

| # | Decision | What the contract will say |
|---|---|---|
| P1 | open (no decision yet) | unchanged; one predictor per deployment until decided |
| P2 | accept | the gateway sends `session_id` as an opaque hash (first 32 hex characters of the sha256 of the MCP session id); format `^[A-Za-z0-9_.-]{1,64}$` |
| P3 | accept | the gateway sends at most the last 200 calls plus `history_total: int` (calls so far) |
| P4 | no change | `ttl_s` stays an integer or null; the service always sends an explicit value |
| P5 | no change | `now_ms` stays; the service validates it and does not use it as a feature |
| P6 | accept | optional response fields `mode` (`ranker` \| `ngram` \| `majority` \| `prior` \| `none`), `degraded` (bool), `truncated` (bool) |
| P7 | accept | the controller merges decisions per server: `copies` = max, `ttl_s` = max, `start_in_ms` = min |
| P8 | accept, modified | batch ids come from an explicit `X-Skypiea-Batch` header when the client sends one; otherwise the gateway groups calls that overlapped in time. The contract states this order |
| P-new | user, 2026-11-04 | the response carries `keep_after_use_s: int >= 0` (top level). After a pod serves a call, the controller applies TTL = max(`ttl_s` from the latest decision for that server, `keep_after_use_s`). Absent = the controller default (30 s); keep-alive baselines keep 30 s; our policy may set it small |
