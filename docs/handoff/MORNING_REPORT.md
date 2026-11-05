# Morning report: 2026-11-05 (orders received 05:30 UTC)

Every number below comes from the Job logs (`~/.cache/skypiea/scenario/logs/`, outside git),
parsed into `results/` by `gateway/scenario_verify.py`. Nothing was hand-edited, smoothed or
dropped. Image tags are on every row.

## Instance and cost

- **Started:** 05:34:04 UTC (`make aws-start`; running at 05:34:24).
- **Dead-man shutdown:** set at 05:34:32 for **08:24 UTC** (`shutdown -h +170`). It is earlier
  than the ordered +3h30 (09:04) so that it falls before the 08:30 hard deadline. The timer is
  lost with the stop, so nothing is pending now.
- **Stopped:** 07:20:30 UTC (`make aws-stop`); state confirmed `stopped` at **07:20:48 UTC**.
- **Cost:**
  - this session: 1.78 h x $0.108/h, about **$0.19**;
  - ECR: one new platform image, 0.6 MB of new layers (negligible);
  - **total so far: about $1.37** (previous total $1.18). While stopped: about $0.06/day for
    the volume.
- **Freeze:** every step started before 07:30 UTC. Nothing was cut.

## What changed vs overnight

| What | Overnight | Morning |
|---|---|---|
| Platform image (gateway, controller, stub) | platform:b652e6b | **platform:38c62ec**, sha256:7646f772180d762d0ce64f47024a3b317db022f3ce535fde4f682e37f6565b5a (`results/aws_image_digests.csv`) |
| Stub `ranked` probabilities | `round(p, 6)`: could sum above 1 + 1e-6, and the gateway rejected those answers (A 20, B 32) | `floor(p * 1e6) / 1e6`: sum <= 1. Test: 3,000 random lists; old code 104 over the limit, new code 0 |
| Keep-alive T | 30 s only | plus `keep_alive_10` and `keep_alive_60`. The stub already took `ttl_s` through `/admin/policy`, so no other platform change was needed |
| 40-session draw | 945 calls, 7 with NaN timing, so the check crashed 3 times | non-finite calls dropped at the draw: **7 calls from 5 sessions**, 938 left (counts and ids in `results/concurrent_sessions.json`) |
| Predictor CPU request | 100m | 100m, then **500m** for the step-4 rerun only (chart value, same image) |
| Predictor image | af000231bc7b | af000231bc7b (unchanged) |

The sessions, seeds and parameters are the same as overnight: 20 sessions per scenario, 5 at
once, unscaled gaps. The A run reused the same `scenario_sessions.json` draw; the 40-session
check used seed 20261105 again.

Rows from the old stub are kept and marked in the `note` column as `rounding-handicapped`: every
Markov-1 row on platform:b652e6b, 435 call rows. This includes the SYNTHETIC PRD Markov-1 rows,
which ran on the same stub; their runs logged 0 rejections.

## Real traces (`results/scenario_aws_{calls,summary}.csv`)

Columns: cold = cold rate; p50/p99 = added latency in ms; idle = idle pod-seconds; queued =
scale-ups queued; hits/misses = gateway prewarm counters; pred err = gateway predictor errors
(all reasons). There were no predictor timeouts in any scenario run: the morning
gateway snapshots (`*.gw`) show none, and every predictive row has 0 errors.

**A = 20 tau2_bench test sessions, 116 calls (support profile for predictive)**

| Policy | Run | Platform | cold | p50 / p99 | idle | queued | hits / misses | pred err |
|---|---|---|---|---|---|---|---|---|
| scale_to_zero | overnight | b652e6b | 0.491 | 67 / 6007 | 350 | 2 | 57 / 59 | 0 |
| always_warm | overnight | b652e6b | 0.000 | 0 / 0 | 6534 | 14 | 116 / 0 | 0 |
| **keep_alive T=10 s** | morning | 38c62ec | 0.155 | 0 / 3866 | 578 | 0 | 98 / 18 | 0 |
| keep_alive T=30 s | overnight | b652e6b | 0.086 | 0 / 3638 | 888 | 0 | 106 / 10 | 0 |
| **keep_alive T=60 s** | morning | 38c62ec | 0.078 | 0 / 3714 | 999 | 0 | 107 / 9 | 0 |
| markov1 (rounding-handicapped) | overnight | b652e6b | 0.155 | 0 / 4121 | 624 | 0 | 98 / 18 | 20 |
| **markov1 (floored stub)** | morning | 38c62ec | 0.164 | 0 / 4801 | 588 | 0 | 97 / 19 | 0 |
| predictive (aabdfd2a9a39) | overnight | b652e6b | 0.155 | 0 / 3648 | 633 | 0 | 98 / 18 | 0 |

**B = 20 TraceLab test sessions of at most 600 s, 319 calls (coding profile, af000231bc7b)**

| Policy | Run | Platform | cold | p50 / p99 | idle | queued | hits / misses | pred err |
|---|---|---|---|---|---|---|---|---|
| scale_to_zero | overnight | b652e6b | 0.589 | 2426 / 6080 | 986 | 9 | 131 / 188 | 0 |
| always_warm | overnight | b652e6b | 0.009 | 0 / 2 | 14675 | 54 | 316 / 3 | 0 |
| **keep_alive T=10 s** | morning | 38c62ec | 0.235 | 0 / 4981 | 1549 | 1 | 244 / 75 | 0 |
| keep_alive T=30 s | overnight | b652e6b | 0.103 | 0 / 5516 | 2216 | 1 | 286 / 33 | 0 |
| **keep_alive T=60 s** | morning | 38c62ec | 0.066 | 0 / 4888 | 2744 | 1 | 298 / 21 | 0 |
| markov1: 3 crashed attempts (NaN warm-up) | overnight | b652e6b | – | – | – | – | – | – |
| markov1 (warm-up fix, rounding-handicapped) | overnight | b652e6b | 0.291 | 0 / 4356 | 1552 | 2 | 226 / 93 | 32 |
| **markov1 (floored stub)** | morning | 38c62ec | 0.288 | 0 / 4988 | 1538 | 2 | 226 / 93 | 0 |
| predictive | overnight | b652e6b | 0.056 | 0 / 5572 | 2807 | 3 | 301 / 18 | 0 |

Every completed policy: ok on every call (A 116/116, B 319/319). The morning B Markov-1
warm-up dropped 1 non-finite call, the same train session #48 as overnight.

### What the numbers say

- **Rounding fix:** it removed every rejection (A 20 -> 0, B 32 -> 0). It did not improve
  Markov-1: A cold 0.155 -> 0.164, B 0.291 -> 0.288 (B prewarm hits and misses identical,
  226 / 93). The overnight Markov-1 conclusions stand.
- **Cost-latency curve, real A:** predictive (cold 0.155, idle 633) lands on the keep-alive
  T=10 s point (0.155, 578). It is no better than keep-alive; T=30 s and T=60 s buy lower cold
  rates with more idle pod-seconds.
- **Cost-latency curve, real B:** predictive (cold 0.056, idle 2807) sits next to keep-alive
  T=60 s (0.066, 2744): 3 fewer misses per 319 calls (18 vs 21, gateway prewarm counters)
  for 2% more idle pod-seconds. Its p99 is worse (5572 vs 4888 ms). Compared with the
  overnight T=30 s alone, predictive looked like a 27%-more-idle trade; against the full curve
  it is about on it, not clearly beyond it.
- Single runs, N=20 per scenario. These are not confidence intervals; differences of a few
  calls are within run-to-run noise.

## SYNTHETIC PRD scenarios (unchanged from overnight)

`results/scenario_aws_prd_SYNTHETIC_*`, dataset=SYNTHETIC on every row, never mixed with the
above. Nothing was rerun this morning. Their Markov-1 rows are now also marked
`rounding-handicapped` (the same stub); those runs logged 0 rejections.

| Policy | PRD A cold | p50 / p99 | idle | PRD B cold | p50 / p99 | idle |
|---|---|---|---|---|---|---|
| scale_to_zero | 0.191 | 0 / 4861 | 143 | 0.450 | 0 / 5036 | 363 |
| always_warm | 0.000 | 0 / 0 | 4013 | 0.000 | 0 / 0 | 9116 |
| keep_alive 30 s | 0.191 | 0 / 4836 | 234 | 0.212 | 0 / 5377 | 993 |
| markov1 | 0.119 | 0 / 2378 | 249 | 0.225 | 0 / 4997 | 843 |
| predictive | 0.119 | 0 / 2892 | 241 | 0.263 | 0 / 4396 | 935 |

## 40-session check, real predictor (`results/concurrent40_aws.csv`)

40 TraceLab test sessions at once, gaps x0.2. This is a functional check, not a latency
result. Predictor af000231bc7b, coding profile.

| Attempt | Platform | Draw | CPU request | calls ok | cold | queued | hits / misses | predictor calls | **timeouts** | checks |
|---|---|---|---|---|---|---|---|---|---|---|
| conc40_a1..a3 (overnight) | b652e6b | 945 calls incl. 7 NaN | 100m | crashed: `int(NaN)` | – | – | – | – | not recorded per attempt (29 seen in Prometheus) | – |
| conc40m_a1 | 38c62ec | NaN dropped, 938 | 100m | **938/938** | 67 | 4 | 870 / 68 | 601 | **337** | all PASS |
| conc40cpu_a1 (step 4) | 38c62ec | NaN dropped, 938 | **500m** | **938/938** | 62 | 3 | 876 / 62 | 598 | **340** | all PASS |

All predictor errors were timeouts (`reason="timeout"`; the gateway's limit is
`SKYPIEA_PREDICTOR_TIMEOUT_MS=300`). Timeouts come from before/after snapshots of the gateway
counters (`conc40*_a1.gw`).

- The NaN fix worked: both runs completed; never more than 2 pods starting; all tools back at
  0.
- **Timeouts recurred** (337 of 601 predictor calls, 56%) and **the one allowed change did
  not help**: with a 500m CPU request, 340 of 598 (57%). The 500m request was confirmed on the
  deployment before the stop. A CPU *request* only reserves scheduler share on a node that
  isn't otherwise saturated, and the limit (1 CPU) was unchanged, so this result is
  consistent with the bottleneck not being CPU reservation. I did not try a second change, as
  ordered. A timed-out decide falls back to no prewarm (FR-4), which is why calls still
  succeeded.
- Candidates for the model window, not tried: per-request latency of the predictor under 40
  concurrent sessions (its `skypiea_predictor_latency_seconds` histogram), the 300 ms gateway
  budget, single-worker serving.

## Verification (a)-(f): all pass

`python -m gateway.scenario_verify` with `SKYPIEA_ACCT` from `aws sts get-caller-identity`
(never printed or written):

- **(a) Expected calls:** A 116, B 319, PRD from the record, 40-session 938 after the drop.
  Each (scenario, policy) has a complete attempt, including keep_alive_10 and keep_alive_60
  on real A and B.
- **(b) ok:** every completed attempt ok on every row. Crashed attempts are kept as error rows.
- **(c) Image tags:** on every row.
- **(d) SYNTHETIC:** only in the PRD files, on every row there.
- **(e) Secrets:** no account id or key-like string.
- **(f) Size:** every file under 1 MB (largest: `scenario_aws_calls.csv`, 659 kB).

Totals: real 3,480 call rows and 19 policy lines (3 of them crashed B Markov-1 attempts);
SYNTHETIC 610 call rows and 10 policy lines; 40-session 5 attempt lines.

## Incidents (all fixed, none affected a measurement)

- **Extra local tags:** `TOOLS="" tools/build_images.sh` fell back to "all tools", so the 16
  tool images also got local `38c62ec` tags. All came from cache in 24 s; none were pushed. Left
  in place, because deleting images is forbidden.
- **10-min stall risk:** the first driver run's gateway-metric snapshot went through the SSM
  retry wrapper. The fresh gateway had no labelled samples, so the empty answer would have
  triggered a 10-min wait. I stopped the driver at 05:35, before any Job, made the snapshot a
  direct call, and restarted it.
- **Timeout parser:** the driver's parser also summed the `_created` timestamp line, so step 4
  was skipped once at 07:08 with a shell error. I fixed it (the verifier had the same pattern
  and got the same fix) and re-ran the driver at 07:09: done steps were skipped and step 4 ran.

## Files

- **Code (commits on feat/infra, local only):**
  - `gateway/predictor_stub.py` (floor);
  - `gateway/scenario_run.py` (keep_alive_10/_60);
  - `gateway/concurrent_check.py` (NaN drop at the draw);
  - `gateway/scenario_verify.py` (new logs, `note` column, timeouts, CPU column);
  - `infra/aws/scripts/morning.sh` (driver);
  - tests in `tests/test_scenario_run.py`.
- **Results:**
  - `results/scenario_aws_{calls,summary}.csv`;
  - `results/scenario_aws_prd_SYNTHETIC_*` (regenerated: `note` column added, numbers
    unchanged);
  - `results/concurrent40_aws.csv`;
  - `results/concurrent_sessions.json`;
  - `results/aws_image_digests.csv`.

## Open, needs your OK

1. Predictor timeouts at 40 concurrent sessions: profile the predictor, which belongs to the
   model window.
2. Hashing the session id in the gateway; the ':' bug report to the model window (both carried
   over from overnight).
