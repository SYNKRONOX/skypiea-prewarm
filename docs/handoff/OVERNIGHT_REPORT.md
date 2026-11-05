# Overnight report, 2026-11-04/05 (feat/infra, local commits only)

**Status:** every planned run finished except the 40-session check: it crashed 3 times on NaN
timings in its own session draw (not fixed: see "Odd things").
- B Markov-1 was rerun with the authorized warm-up fix and is complete.
- The instance was stopped at 01:09:15 UTC; state confirmed `stopped` by `aws ec2 describe-instances` at 01:11:09 UTC.
- Nothing was pushed or merged. No AWS resource was created tonight; the ECR repo
  skypiea/predictor was created earlier in the day, with approval.

## Instance and cost

- **Started:** 2026-11-04 21:14:30 UTC.
- **Stopped:** 2026-11-05 01:09:15 UTC (`make aws-stop`); state confirmed `stopped` by `aws ec2 describe-instances` at 01:11:09 UTC.
- **Dead-man shutdown:** scheduled on the node at 22:46 UTC for **03:50 UTC** (`shutdown -h
  03:50`). It is earlier than "now + 420 min" (05:26) so that it lands before the 04:00 hard
  deadline. The instance's shutdown behaviour is stop. The timer does not survive a stop, so
  nothing is pending now.
- **Cost:**
  - tonight's session: 3.91 h x $0.108/h, about **$0.42**;
  - ECR storage for the 3 predictor images (mostly shared layers): about $0.01/month;
  - **total so far: about $1.18** (previous total $0.76).
  - While stopped: about $0.06/day for the 20 GB volume.

## What ran

All runs: platform:b652e6b (gateway and controller unchanged), unscaled gaps, 20 sessions
per scenario, 5 at once, every call recorded. Image tags are on every row.
- **Real traces** (`results/scenario_aws_{calls,summary}.csv`):
  - A = 20 tau2_bench test sessions (116 calls), predictor profile support.
  - B = 20 TraceLab test sessions of at most 600 s (319 calls), profile coding (retuned,
    af000231bc7b).
  - Session ids: `results/scenario_sessions.json`.
- **SYNTHETIC PRD scenarios** (`results/scenario_aws_prd_SYNTHETIC_*`):
  - PRD A (`syn_branching`, 42 calls) and PRD B (`syn_deterministic`, 80 calls), profile prd,
    image af000231bc7b.
  - Session ids: `results/scenario_sessions_prd_SYNTHETIC.json`.
  - These are not evidence about real workloads.
- **Columns:**
  - "cold" = the call found no ready pod and waited for a cold start.
  - "added" = gateway-added latency per call.
  - Idle pod-s = the controller's `skypiea_pod_seconds_idle_total`, from just before the
    policy's set-up until every tool was back at 0.
  - p99 is nearest-rank: over 116 / 319 / 42 / 80 calls it is the 2nd highest, 4th highest
    and twice the highest call respectively, so treat it as a tail indicator.
  - Prewarm hits/misses = calls that found / did not find a ready pod on arrival, whatever
    the cause (a pod still up from an earlier call counts too).

### Real A (tau2, support; predictor:aabdfd2a9a39, whose support bundle is the same in af000231bc7b)

| policy | model / image | calls ok | cold rate | added p50 ms | added p99 ms | idle pod-s | queued scale-ups | prewarm hits / misses | predictor errors | attempt log |
|---|---|---|---|---|---|---|---|---|---|---|
| scale_to_zero | stub / predictor:aabdfd2a9a39 | 116/116 | 0.491 | 67 | 6007 | 350 | 2 | 57 / 59 | 0 | A_stub.log |
| always_warm | stub / predictor:aabdfd2a9a39 | 116/116 | 0.000 | 0 | 0 | 6534 | 14 | 116 / 0 | 0 | A_stub.log |
| keep_alive | stub / predictor:aabdfd2a9a39 | 116/116 | 0.086 | 0 | 3638 | 888 | 0 | 106 / 10 | 0 | A_stub.log |
| markov1 | stub / predictor:aabdfd2a9a39 | 116/116 | 0.155 | 0 | 4121 | 624 | 0 | 98 / 18 | 20 | A_stub.log |
| predictive | lgbm-support-aabdfd2 / predictor:aabdfd2a9a39 | 116/116 | 0.155 | 0 | 3648 | 633 | 0 | 98 / 18 | 0 | A_pred.log |

### Real B (TraceLab, coding retuned; baselines ran before the swap, image tag as recorded)

| policy | model / image | calls ok | cold rate | added p50 ms | added p99 ms | idle pod-s | queued scale-ups | prewarm hits / misses | predictor errors | attempt log |
|---|---|---|---|---|---|---|---|---|---|---|
| scale_to_zero | stub / predictor:aabdfd2a9a39 | 319/319 | 0.589 | 2426 | 6080 | 986 | 9 | 131 / 188 | 0 | B_stub_a1.log |
| always_warm | stub / predictor:aabdfd2a9a39 | 319/319 | 0.009 | 0 | 2 | 14675 | 54 | 316 / 3 | 0 | B_stub_a1.log |
| keep_alive | stub / predictor:aabdfd2a9a39 | 319/319 | 0.103 | 0 | 5516 | 2216 | 1 | 286 / 33 | 0 | B_stub_a1.log |
| markov1 | stub / predictor:af000231bc7b | 319/319 | 0.291 | 0 | 4356 | 1552 | 2 | 226 / 93 | 32 | B_m1fix_a1.log |
| markov1 | predictor:aabdfd2a9a39 | FAILED | - | - | - | - | - | - | - | B_stub_a1.log: ValueError: Out of range float values are not JSON compliant: nan |
| markov1 | predictor:aabdfd2a9a39 | FAILED | - | - | - | - | - | - | - | B_stub_a2.log: ValueError: Out of range float values are not JSON compliant: nan |
| markov1 | predictor:aabdfd2a9a39 | FAILED | - | - | - | - | - | - | - | B_stub_a3.log: ValueError: Out of range float values are not JSON compliant: nan |
| predictive | lgbm-coding-af00023 / predictor:af000231bc7b | 319/319 | 0.056 | 0 | 5572 | 2807 | 3 | 301 / 18 | 0 | B_pred_a1.log |

### SYNTHETIC PRD A (profile prd)

| policy | model / image | calls ok | cold rate | added p50 ms | added p99 ms | idle pod-s | queued scale-ups | prewarm hits / misses | predictor errors | attempt log |
|---|---|---|---|---|---|---|---|---|---|---|
| scale_to_zero | stub / predictor:af000231bc7b | 42/42 | 0.191 | 0 | 4861 | 143 | 0 | 34 / 8 | 0 | PRDA_stub_a1.log |
| always_warm | stub / predictor:af000231bc7b | 42/42 | 0.000 | 0 | 0 | 4013 | 14 | 42 / 0 | 0 | PRDA_stub_a1.log |
| keep_alive | stub / predictor:af000231bc7b | 42/42 | 0.191 | 0 | 4836 | 234 | 0 | 34 / 8 | 0 | PRDA_stub_a1.log |
| markov1 | stub / predictor:af000231bc7b | 42/42 | 0.119 | 0 | 2378 | 249 | 0 | 37 / 5 | 0 | PRDA_stub_a1.log |
| predictive | lgbm-prd-a7642d0 / predictor:af000231bc7b | 42/42 | 0.119 | 0 | 2892 | 241 | 0 | 37 / 5 | 0 | PRDA_pred_a1.log |

### SYNTHETIC PRD B (profile prd)

| policy | model / image | calls ok | cold rate | added p50 ms | added p99 ms | idle pod-s | queued scale-ups | prewarm hits / misses | predictor errors | attempt log |
|---|---|---|---|---|---|---|---|---|---|---|
| scale_to_zero | stub / predictor:af000231bc7b | 80/80 | 0.450 | 0 | 5036 | 363 | 0 | 44 / 36 | 0 | PRDB_stub_a1.log |
| always_warm | stub / predictor:af000231bc7b | 80/80 | 0.000 | 0 | 0 | 9116 | 14 | 80 / 0 | 0 | PRDB_stub_a1.log |
| keep_alive | stub / predictor:af000231bc7b | 80/80 | 0.212 | 0 | 5377 | 993 | 0 | 63 / 17 | 0 | PRDB_stub_a1.log |
| markov1 | stub / predictor:af000231bc7b | 80/80 | 0.225 | 0 | 4997 | 843 | 0 | 62 / 18 | 0 | PRDB_stub_a1.log |
| predictive | lgbm-prd-a7642d0 / predictor:af000231bc7b | 80/80 | 0.263 | 0 | 4396 | 935 | 0 | 59 / 21 | 0 | PRDB_pred_a1.log |

### 40-session check with the real predictor (coding, af000231bc7b)

**Not completed.** 3 attempts, all crashed before any summary
(`results/concurrent40_aws.csv`): `ValueError: cannot convert float NaN to integer`.
- **Cause:** 5 of the 40 drawn TraceLab test sessions (seed 20261105) contain 7 calls with
  `service_ms` = NaN. Ids: claude:b69fb19d-a250-20d9-d47f-1a9854f5248f,
  claude:6b634a20-e13b-798c-b8e3-d77c3dcdca2e, claude:c13e13d9-254f-d746-7451-b8485f2fdeb0,
  claude:1adc315e-de6c-271c-1339-23c49f2a9e33, claude:6631ad35-cddc-3d73-5098-39a427faf4cd.
- **Calls before the crash:** the sessions run concurrently, so some calls had already gone
  out. They were not recorded. During these attempts the gateway logged 29
  `predictor_errors{reason="timeout"}` (Prometheus, at about 00:37), so the predictor took
  longer than 300 ms on some of the 40-at-once calls. Not measured further.
- **Queue metrics and errors for this check:** none recorded.

## Did predictive beat the baselines? (plain words, no tuning done)

- **Real B (coding, retuned):** **yes on cold rate, no on the tail.**
  - Predictive had the lowest cold rate of the scale-to-zero policies:
    - predictive 5.6%;
    - keep-alive 30 s 10.3%;
    - Markov-1 29.1%;
    - scale-to-zero 58.9%;
    - only always-warm was lower, at 0.9%.
  - It paid for that with **27% more idle pod-seconds than keep-alive** (2807 vs 2216).
    That is still 5.2x less than always-warm (14675).
  - Its p99 added latency (5572 ms) is no better than keep-alive's (5516 ms): the calls it
    misses still take a full cold start.
- **Real A (tau2, support):** **no.**
  - Predictive tied Markov-1 on cold rate (15.5% both) with about the same idle pod-s
    (633 vs 624).
  - Keep-alive 30 s was clearly better on cold rate (8.6%) for 40% more idle pod-s (888).
- **SYNTHETIC PRD A:** predictive tied Markov-1 exactly (11.9% cold, 241 vs 249 pod-s).
  Both were better than keep-alive and scale-to-zero (19.0%).
- **SYNTHETIC PRD B:** **no.**
  - Predictive was 26.3% cold, worse than keep-alive (21.3%) and Markov-1 (22.5%).
  - Its idle pod-s (935) sit between Markov-1 (843) and keep-alive (993).
- **Caveat on Markov-1 (real A and B):** it was handicapped by a stub bug (below). Its
  numbers are slightly pessimistic.

## Odd things, failures, retries

1. **B Markov-1:**
   - **What failed:** 3 failed attempts (B_stub_a1..a3, identical parameters). One TraceLab
     **train** session used for the warm-up (`codex:ecf838ae-048e-93f2-8940-85411d567e58`,
     warm-up #48 of 300, batch 12 `exec_command:shell_other`) has `service_ms` = NaN with
     `has_timing` true, and httpx refuses NaN in JSON.
   - **Fix (as authorized, at 00:46 UTC):** the warm-up now drops calls with non-finite
     timing. 1 call was dropped, from warm-up #48. Commit ca7c30c.
   - **Rerun:** once (B_m1fix_a1), 319/319 ok. The three failures stay in the CSV.
   - **Images:** the rerun recorded predictor:af000231bc7b. The stub served the policy, so
     the predictor image does not matter for it; the other B baselines recorded aabdfd2a9a39.
2. **Stub Markov-1 contract rejections:**
   - **Cause:** the stub rounds each probability to 6 decimals. A ranked list can then sum
     to 1.000001-1.000002, which is above the gateway validator's 1e-6 tolerance. The
     gateway rejects that answer and the call takes the cold path.
   - **Counted as `predictor_errors{reason="contract"}`:** 20 in real A, 32 in real B.
     Reproduced offline: 21/116 and 37/319 answers.
   - **Not fixed:** the stub lives in the platform image, which must not change during
     the runs.
3. **Scale-to-zero cold rate is not 100%** (A 49%, B 59%, PRD A 19%, PRD B 45%). A pod
   started by a call stays up for its 5 s purge and 5 s minimum dwell. Calls in that window
   (the same session's next call, or one of the other 4 sessions) find it warm. This is the
   system as configured, not an error.
4. **Always-warm queued scale-ups** (14 in A and in both PRD runs, 54 in B): its standing
   pool and per-call warms go through the 2-per-node starting limit. None expired in the
   queue.
5. **No call failed** in any completed policy: ok = 2175/2175 real rows and 610/610
   SYNTHETIC rows.
6. **Session-id limitation (documented in docs/gateway_controller.md):** the gateway
   forwards the client's session id unchanged. Ids outside `^[A-Za-z0-9_.-]{1,64}$` get
   the cold path. The replayer used hex ids, so no run was affected.

## Verification (gateway/scenario_verify.py)

- (a) Call counts: A 116 x 5, B 319 x 5 (Markov-1 from the rerun), PRD A 42 x 5, PRD B
  80 x 5 are all present. **40-session: missing** (crashed, above).
- (b) ok=True on every row of the completed policies. The failed attempts are listed as
  POLICY_ERROR rows.
- (c) predictor_image and platform_image are on every row. The first A and B logs predate
  the field; their tags come from this session's deploy record.
- (d) dataset=SYNTHETIC on every PRD row, and only there. Separate files.
- (e) No account id or key in the written files (exact account-id check from the
  environment, never printed).
- (f) Every file is under 1 MB; the largest is scenario_aws_calls.csv at 391 KB.

## Left for the morning

1. **40-session check:** apply the same non-finite-timing drop to the concurrent check's
   sessions (or exclude the 5 sessions), then rerun it with the real predictor. It needs
   your OK, because it changes that check's input.
2. **Stub rounding:** fix the stub's probability rounding (platform image rebuild). Then
   decide whether to rerun Markov-1 (A, B) without the 20 / 32 rejected answers.
3. **Session id:** hash it in the gateway (contract proposal P2).
4. **Predictor timeouts:** look at the 29 timeouts seen when 40 sessions started at once.
5. **Bug for the model window:** a `session_id` containing `:` degrades the predictor to
   mode=none.
