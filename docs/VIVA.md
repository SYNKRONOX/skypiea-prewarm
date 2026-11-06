# Viva preparation (DS252 final evaluation, 6 Nov 2026)

For the team (G. Parthav, L. Mohnish, Ch. Dhanunjaya). Every number has its source file next
to it. **UNSOURCED** marks a number or statement with no committed file behind it. If a number
here disagrees with its source, the source is right. Commit hashes in brackets are the commit
that last changed the source file, as recorded in `docs/numbers_and_sources.md` or
`docs/results_summary.md`.

Main sources:

- `README.md`: architecture and the result tables shown to readers
- `docs/results_summary.md`: generated from committed result files only
- `docs/numbers_and_sources.md`: every number we say out loud, with its reason
- `docs/limits.md`: what the results do not show
- `docs/model.md`: predictor, profiles, simulator, tuning disclosures
- `docs/gateway_controller.md`: gateway, operator, cap of 2
- `docs/prd_excerpts.md`: PRD requirement text, verbatim
- `results/*`: raw result files

- `docs/DEMO.md`, `docs/AGENT.md`, `docs/LIVE_CONSOLE.md`: the demos (`make demo`,
  `make agent-demo`, `make live`), all merged into `main`
- `results/live_console_demo/`, `results/live_console_leadtime/`: Live Console demo runs (not
  results)

Updated 2026-11-06 (final documentation pass): the demos are merged, section 3 now leads with
the Live Console, and sections 4.8-4.9, 5, 6 and 8 cover the lead-time and console findings.

---

## 1. Pitch

### 30 seconds

AI agents call tools in patterns: a support agent that looks up an order usually checks the
refund policy next. When every tool runs as a scale-to-zero container, the first call to a cold
tool waits for a pod to start. On our AWS node that wait is a median of 2.6 to 8.4 seconds,
depending on the tool (`README.md`, E0 table). Skypiea learns the patterns from tool-call
metadata only, never prompts or arguments. It starts the next tool's pod while the LLM is still
thinking, and lets it go back to zero afterwards. On real AWS runs it cuts cold calls from 49%
to 16% (support) and from 59% to 6% (coding) compared with plain scale-to-zero
(`results/scenario_aws_summary.csv`). It uses a tenth to a fifth of always-warm's idle
pod-seconds (ratios computed from the same file).

### 2 minutes

**The problem.** An agent alternates between thinking (the LLM produces its next step) and
calling a tool. If every tool is a Kubernetes Deployment scaled to zero, nothing runs while
idle, which is cheap, but the first call to a cold tool waits for a pod to be scheduled, pull
its image, start Python and load its data. On our AWS node one pod starting alone takes about
2.7 s with the image cached and 5.4 s uncached (median of all E0 runs pooled over tools,
`docs/numbers_and_sources.md`, from `results/e0_aws_runs.csv`). Keeping everything warm
removes the wait but pays for idle pods. The LLM's think time is the opportunity: if we know
which tool comes next, we can start its pod during the think gap.

**What Skypiea is.** Four parts (`README.md`, "It has four parts"):

1. **Gateway**: one MCP endpoint (`POST /mcp`). It routes each call to a ready pod, holds a call
   up to 150 ms if a pod is about to be ready, and after replying sends the session's call
   metadata to the predictor.
2. **Operator**: our own kopf scale-to-zero controller on plain Deployments. It scales tools
   0..N on warm requests, starts at most 2 pods at once per node, and scales back to 0 after
   the keep-alive.
3. **Predictor**: a Markov/n-gram transition graph plus a LightGBM re-ranker, and a warm policy
   that decides which tools to start, when, and how many copies, under an idle budget.
4. **CLI**: `skypiea status / policy get / policy set / sessions / trace / explain`.

**Where it runs.** Locally on kind (one control plane, one worker, local registry). This is for
functional checks only, and kind timings are a lower bound. For measurements it runs on k3s on
one AWS EC2 m7i-flex.large (2 vCPU) in ap-south-1, with images in ECR, no inbound ports (SSM
only), at about $0.108/h (`README.md`, "AWS").

**What we found.** Prediction clearly beats scale-to-zero on cold calls and always-warm on idle
cost. Against a well-tuned fixed keep-alive the result depends on the regime. In the simulator
over hundreds of held-out sessions, the shipped profiles warm 3.4 to 18.2 points more calls
than keep-alive at the same idle cost (`README.md` Summary; `results/sim_sensitivity.csv.gz`).
In the 20-session AWS runs, predictive lands on the keep-alive curve, not above it, and the CIs
overlap.

---

## 2. Architecture: one request, agent to warm pod

Sources: `README.md` "Architecture"; `docs/gateway_controller.md`; `docs/model.md` "Warm
decisions".

```
agent --POST /mcp--> gateway --forward--> ready tool pod (0..N per tool)
                       |  ^ EndpointSlices
        (after reply)  v  |
                   predictor (/decide) --warm decisions--> gateway --POST /v1/warm--> operator
                                                                               scales 0 <-> N
```

1. **The agent sends a tool call.** A JSON-RPC `tools/call` arrives at `POST /mcp` with an
   `Mcp-Session-Id` header. The gateway creates a session id if there is none. Each session
   keeps its own history; sessions never see each other.
2. **The gateway maps the tool name to a server.** `tools/tool_map.yaml` maps a tool class or
   server name to one of 16 servers. Anything unknown goes to `generic_fallback`.
3. **Ready pod?** The gateway watches EndpointSlices. If a pod of that server is Ready, it sends
   the call directly to the pod address (round-robin), not via the Service ClusterIP.
   kube-proxy takes 0.1 to 0.7 s after Ready to program a new pod (`docs/gateway_controller.md`,
   E0). This is a **warm** call.
4. **No ready pod: the hold.** The gateway asks the operator for a pod (`reason=demand`) and
   holds the call up to **150 ms** in case a pod that is already starting becomes Ready (PRD
   FR-9). If none does, the call takes the **cold path**: it waits for Ready (up to 120 s) and
   is marked cold (`X-Skypiea-Cold: 1`). After 120 s it fails with JSON-RPC error -32001; it
   never hangs.
5. **The tool pod answers** and the gateway returns the reply to the agent.
6. **Predictor, off the critical path.** Only *after* the reply has been sent, the gateway posts
   the session history (tool names, timings, batch structure; no arguments) to the predictor's
   `/decide`, with a 300 ms timeout.
7. **The predictor ranks and decides.** A fail-open ladder (ranker, then n-gram, then majority,
   then none) produces next-tool probabilities within a 40 ms budget (`docs/model.md`). The
   warm policy of the deployment's profile (`support`, `coding` or SYNTHETIC `prd`) turns them
   into decisions: which server, how many copies, start now or in x ms, and the TTL. If the
   predictor is unsure it abstains, and the system behaves as plain scale-to-zero.
8. **The gateway forwards each warm decision** to the operator (`POST /v1/warm`) after
   `start_in_ms`.
9. **The operator scales.** Every 250 ms it reconciles the desired replicas and patches the
   Deployment. At most 2 pods may be starting at once per node; the rest wait in a queue
   ordered by due time. Hard caps: 3 replicas per server, 20 in total.
10. **The LLM thinks; the pod starts.** If the think gap is longer than the cold start, the pod
    is Ready when the next call arrives, and that call is warm (step 3).
11. **Back to zero.** A pod unused for its TTL (default 30 s, PRD FR-3) is removed within
    TTL + 5 s. The operator never scales below the number of in-flight calls.
12. **Observability.** The gateway and operator export Prometheus metrics (cold or warm per
    call, added latency, idle pod-seconds, queue depth) to Grafana. The CLI reads the same
    state through the kubeconfig.

**Failure behaviour (FR-4).** If the predictor is dead, slow, or returns an invalid answer, the
call is unaffected; the system becomes scale-to-zero. Tested by live failure injection on kind
(`docs/gateway_controller.md`, "Live failure injection on kind").

---

## 3. 7-minute demo script

Roles: **Parthav (P)**: predictor and simulator. **Mohnish (M)**: traces, replayer, data.
**Dhanunjaya (D)**: controller, cluster, cloud. Roles follow the table in `README.md`.

**Preferred: the Live Console in story mode.** `make live` (after `make fetch-models` or with
local bundles), then follow the **5-minute presenter script in `docs/DEMO.md`** ("Live demo:
5-minute presenter script"; a 1-minute version is under it). It is built around the **Story**
buttons, which send prepared customer lines to the **live** model (nothing pre-recorded):
Act 1 a new customer books a trip (boarding-pass card, prices quoted from tool results),
Act 2 a schedule change (truth check: "Not done" and "Not from a tool: $X" badges), Act 3 the
predictor is killed mid-conversation (every call still succeeds; warm calls say why they were
warm, never "predicted"). Optional: Demo → Code fix → Act (Dev assistant fixes a planted
`merge_sort` bug; the test runner is **simulated**, no model-written code runs). Say the three
plain facts from `docs/LIVE_DEMO.md`: the tool containers are real and cold-start for real;
the airline data and the repository are mocks inside the console; the predictor sees only tool
names and timing.

If asked "which model and why": llama3.1:8b by default, chosen by correctness in a bake-off
against qwen2.5:7b on five scripted personas (`docs/LIVE_DEMO.md`, "Model bake-off": 8/10 vs
6/10 tasks completed, 0 vs 4 false "done" claims; demo runs, n = 10 each). The Model dropdown
switches live; switching starts a new conversation.

If asked about **FR-9** ("the AWS runs show held_ms up to 397.2 ms but the cap is 150 ms"): the
recorded value was measured from the call's arrival to the moment the event loop resumed it;
the wait itself is `wait_ready(timeout = 150 ms)`. Overrun above 150 ms: p50 1.0 ms, p99 24 ms;
the 397.2 ms call is a single outlier (`python -m gateway.hold_report`). The gateway now
reports `held_ms` (the hold itself, never above the cap, tested), `hold_late_ms` and
`hold_wait_ms` (`docs/PRD_STATUS.md` FR-9). **FR-6**: a reload of the real model under 8
request threads took 0.49-0.50 s and served every in-flight request
(`results/fr6_reload_check.json`; laptop, not AWS).

**Alternative (no console): `make demo` + `make agent-demo`**, per **`docs/DEMO.md`** and
**`docs/AGENT.md`**. The table below is for this path. Run whichever you choose once on the
demo laptop before the viva.

**Say during the demo:** it runs on kind, on a laptop. Timings shown live are functional only.
All reported timings come from AWS (`README.md`, "kind is for functional checks only").

| Time | Who | Action | What to say |
|---|---|---|---|
| 0:00-0:40 | P | Title slide, then the architecture slide | 30-second pitch (section 1). "Four parts: gateway, operator, predictor, CLI." |
| 0:40-1:30 | D | Terminal 1: `make demo` per `docs/DEMO.md` (cluster already up). Terminal 2: `kubectl -n skypiea-tools get pods -w` | "Sixteen MCP tool servers, all at zero replicas. This is our own kopf operator. It starts at most 2 pods at once on a node, because 3 at once on our 2-vCPU AWS node doubled every cold start." |
| 1:30-2:30 | D | `bin/skypiea --context kind-skypiea policy set scale_to_zero`, send a call | "Scale-to-zero: the call waits for a pod to start. On AWS that is about 2.7 s cached and 5.4 s uncached, per our E0 measurements." Point at the pod going 0 to 1. |
| 2:30-4:30 | M then P | `make agent-demo` per `docs/AGENT.md`: a live agent on local Ollama sends calls through the gateway with the predictive policy | M: "The agent sees one MCP endpoint. Between calls the LLM is thinking." P: "After each call the predictor gets only tool names and timings, ranks the next tool, and the operator starts its pod during the think gap." Point at the pod starting before the next call arrives. |
| 4:30-5:15 | P | `bin/skypiea --context kind-skypiea explain <sid>` and `trace <sid>` | "For each call: what was predicted, with what probability, what was warmed, and whether the call was warm or cold." |
| 5:15-6:30 | M | Results slide (section 4 tables) | "On AWS, 20 real sessions per policy: 49% to 16% cold in support, 59% to 6% in coding, versus scale-to-zero. Against a tuned keep-alive we are on its curve, not above it, and the CIs overlap. The simulator, on hundreds of held-out sessions, shows +3.4 to +18.2 points at equal idle." |
| 6:30-7:00 | D | Pods return to 0 in terminal 2; limits slide | "Back to zero after the TTL. Limits: one 2-vCPU node, 20 sessions, a simulator without a shared pool. All of them are in docs/limits.md." |

### Fallback if the live part fails

- **Agent or Ollama fails** (model slow to load, no output): stop after 20 s. Say "the live
  model is slow on this laptop, so here is the recorded run" and show the **recorded session**:
  `docs/live_backup/` (newest: `docs/live_backup/w1/`; console screenshots `docs/img/console_backup_*.png`, `export.csv`,
  `session_log.txt`) or `docs/demo_transcript.log` for `make demo`. Walk through the same
  points from it: call, predicted next tool, pod warmed, next call warm.
- **Cluster fails** (`make demo` errors, pods stuck Pending): do not debug live. Show the
  recorded transcript, then go straight to the results slide. D says one sentence on the cause
  if it is obvious.
- **A call is cold during the predictive demo:** expected sometimes. Say "on kind, with short
  think gaps, a correct prediction can still lose to the cold start".
  `docs/gateway_controller.md` records this: "With x0.2 gaps (0.5-2.7 s) a correct prediction
  cannot beat a 2-4 s kind cold start."
- Keep Grafana (`make grafana-kind`) open in a background tab as a second visual fallback.

---

## 4. Results we claim

### 4.1 E0: cold starts, kind vs AWS

Source: `results/e0_aws_summary.csv`, `results/e0_aws_p3_summary.csv`,
`results/e0_kind_summary.csv`, via `README.md` and `docs/results_summary.md` ("E0 cold starts").
n = 10 per tool and mode. Total = scale request until the first successful MCP call.

| | AWS cached | AWS uncached | AWS cached, 3 at once | kind cached | kind uncached |
|---|---|---|---|---|---|
| Fastest tool, median ms | 2,572 (refund_issue) | 4,301 (read_file) | 5,224 (promo_code_lookup) | 1,960 (format_diff) | 3,332 (shell) |
| Slowest tool, median ms | 3,791 (run_tests) | 8,366 (run_tests) | 6,198 (policy_check) | 3,948 (run_tests) | 6,321 (run_tests) |

Aggregate over tools (`docs/gateway_controller.md`, "Why at most 2 scale-ups"): 1 pod at a time
cached 2,734 ms median, of which app_init 1,211 ms; 3 at once cached 5,541 ms, app_init
3,672 ms. Uncached: 5,360 vs 9,020 ms.

What to say: kind uses a local registry, so it is a **lower bound**. AWS pulls from ECR in the
same region. Starting 3 pods at once roughly doubles the cold start, and almost all of the extra
time is CPU-bound Python start-up on 2 vCPUs.

### 4.2 Real AWS scenario runs (20 sessions per policy, 95% session-bootstrap CI)

Source: `results/scenario_aws_summary.csv`, `results/scenario_aws_calls.csv`, via
`docs/results_summary.md` ("AWS scenario runs"). The CI comes from 1,000 resamples of sessions
(seed 0). Idle pod-s is one run-level counter with no CI.

| Scenario | Policy | cold % [95% CI] | idle pod-s |
|---|---|---|---|
| A support (116 calls) | scale-to-zero | 49.1 [40.6, 58.8] | 350 |
| A | keep-alive 10 s | 15.5 [9.7, 23.2] | 578 |
| A | keep-alive 30 s | 8.6 [3.8, 14.5] | 888 |
| A | keep-alive 60 s | 7.8 [2.9, 13.4] | 999 |
| A | **predictive** | **15.5 [10.2, 21.8]** | **633** |
| A | always-warm | 0.0 [0.0, 0.0] | 6,534 |
| B coding (319 calls) | scale-to-zero | 58.9 [44.2, 74.1] | 986 |
| B | keep-alive 10 s | 23.5 [17.1, 31.3] | 1,549 |
| B | keep-alive 30 s | 10.3 [6.1, 16.7] | 2,216 |
| B | keep-alive 60 s | 6.6 [3.1, 11.8] | 2,744 |
| B | **predictive** | **5.6 [2.3, 10.6]** | **2,807** |
| B | always-warm | 0.9 [0.0, 3.4] | 14,675 |

Markov-1 top-1 rows: A 16.4 [11.0, 23.1], B 28.8 [21.9, 35.9] (floored-stub rerun, platform
38c62ec). The overnight Markov-1 rows (marked †) were rounding-handicapped; see `README.md`.

Claims:

- **vs scale-to-zero:** far fewer cold calls; the CIs do not overlap in either scenario.
- **vs always-warm:** predictive idles 633 vs 6,534 pod-s in A (9.7%) and 2,807 vs 14,675 in B
  (19.1%). The percentages are computed from the table values; they are not in a results file.
- **vs keep-alive:** on the curve, not above it. A matches keep-alive 10 s (15.5% cold at 633
  vs 578 idle pod-s). B sits next to keep-alive 60 s (5.6% vs 6.6% at 2,807 vs 2,744), with a
  worse p99 added latency (5,572 vs 4,888 ms). The CIs overlap.
- **vs Markov-1:** about the same in A, clearly better in B (5.6 vs 28.8; CIs do not overlap).
- The two nights used two platform images (b652e6b and 38c62ec); see the `platform` column.

### 4.3 Simulator: equal-idle margins over keep-alive (TEST)

Source: `results/sim_sensitivity.csv.gz` (64e65c9), via `docs/results_summary.md`. Margin = our
warm % minus keep-alive's warm % at the same idle pod-seconds, interpolated log-linearly between
keep-alive 5 s and 600 s. Primary setting: AWS cold starts, cap 2, parallel_3 contention.

| Source (sessions) | Profile | cached | uncached |
|---|---|---|---|
| TraceLab | coding (shipped, retuned) | +3.5 | +3.4 |
| Copilot | coding (shipped, retuned) | +5.1 | +5.0 |
| tau2 | support | +15.7 | +18.2 |
| TraceLab | coding, frozen (before) | **-1.9** | **-2.4** |

Example: support on tau2 (cached, AWS + cap) reaches 61.4% warm at 11,859 idle pod-s per 1k
calls; keep-alive 30 s reaches 56.4% at 18,218 (`docs/numbers_and_sources.md`).

Say it this way: the shipped coding profile was **adopted after its TEST margins had been
seen** (selected on VAL). The frozen profile is below keep-alive on TraceLab under the cap. Both
facts are in every table (`docs/limits.md`).

### 4.4 Predictive vs scale-to-zero and always-warm (simulator)

Source: `docs/results_summary.md` ("Baselines and FR-2"), from `results/sim_offline.csv.gz`.
In this table "ours" is the frozen coding profile. Always-warm on TraceLab cached is 87.8% warm at
2,375,346 idle pod-s per 1k; on tau2 cached 100.0% at 50,923. Support reaches 61.4% at 11,859.
Oracle (knows every future call; cap-aware, cached, AWS cap 2): TraceLab 72.5% at 1,269, tau2
72.5% at 532 (`README.md` "Oracle"). The oracle shows the remaining headroom for better
prediction.

### 4.5 NFR-3 (idle overhead vs always-warm)

PRD NFR-3 (`docs/prd_excerpts.md`): CPU-seconds on pods never used must be at most 30% of the
always-warm baseline's total CPU-seconds. **We tuned to our own, stricter-to-explain reading:**
idle pod-seconds at most 30% of always-warm's idle (`docs/limits.md`, `configs/model/sim.yaml`
`nfr3_idle_ratio: 0.3`). Both ratios are in every simulator row.

Source: `results/sim_sensitivity.csv.gz`, columns `idle_over_always_warm` (our reading) and
`nfr3_unused_over_always_warm` (PRD reading), run `aws_cap2_parallel_3`, TEST. These columns are
not printed in `docs/results_summary.md`; they were read directly from the file.

| Source | Images | Profile | our reading (idle / always-warm idle) | PRD reading (unused CPU-s / always-warm CPU-s) |
|---|---|---|---|---|
| TraceLab | cached | coding (shipped) | 0.0115 | 0.0020 |
| TraceLab | uncached | coding (shipped) | 0.0136 | 0.0034 |
| Copilot | cached | coding (shipped) | 0.0519 | 0.0104 |
| Copilot | uncached | coding (shipped) | 0.0589 | 0.0146 |
| tau2 | cached | support | 0.2329 | 0.0074 |
| tau2 | uncached | support | 0.2355 | 0.0083 |

All are under 0.30 in both readings. The budget binds only for support: on tau2, keep-alive
30 s alone uses 0.3578 (cached), above the budget (same file). On AWS, predictive idle
pod-seconds were 9.7% (A) and 19.1% (B) of always-warm's (section 4.2). Those are idle
pod-seconds, not the PRD's CPU-seconds.

### 4.6 Other numbers we may be asked about

- **Predictor service latency:** p99 13.2 ms at 75 req/s and 15.9 ms at 150 req/s, open loop, 1
  worker, on the laptop, service alone (`results/loadtest.csv`). PRD target is 50 ms (FR-1).
  Not end-to-end, not on AWS.
- **Next-tool top-1 (E1, TEST):** ranker 0.529 [0.48, 0.57] vs n-gram 0.454 vs Markov-1 0.399 on
  TraceLab. The ranker loses on tau2: 0.520 vs 0.620 (`results/p1_accuracy.csv.gz`,
  `results/p4_ranker.csv`).
- **FR-2 (support, tau 0.4):** 86.8% / 90.9% of predicted calls were Ready on arrival, cached /
  uncached, on 76 / 44 predicted calls (`results/sim_offline.csv.gz`). Not at the PRD's tau 0.6.
- **Hideable calls:** the share of calls whose think gap is at least the AWS cold start.
  TraceLab 91.1% cached / 67.8% uncached; Copilot 79.1 / 56.0; tau2 78.1 / 56.1
  (`results/sim_hideable.csv`).

### 4.7 The honest sentence about keep-alive

> Against a tuned fixed keep-alive the result is regime-dependent. In the simulator, over
> hundreds of held-out sessions with AWS cold starts and the scale-up cap, the shipped profiles
> warm 3.4 to 18.2 points more calls than keep-alive at equal idle cost. In the real AWS runs
> with 20 sessions per policy, predictive sits on the keep-alive curve (it matches keep-alive
> 10 s in Scenario A and keep-alive 60 s in Scenario B) and the cold-rate CIs overlap, so those
> runs cannot separate them.

Sources: `README.md` Summary; `docs/results_summary.md`.

---

### 4.8 Lead-time experiments (live console demo runs + simulator, 2026-11-06)

Sources: `results/live_console_leadtime/tables.md` (36 autopilot conversations, 3 per cell)
and `results/sim_lookahead_val.md` / `_test.md`. **These are demo runs, not results**:
local kind, llama3.1:8b, an assumed typing speed of 200 characters per minute for the
scripted customer, console-only tool mapping, n = 3 per cell.

- **The typing-start hint did not help the first call.** The scripted first message takes
  about 39-43 s to "type", which is longer than the warm TTL (30 s + 5 s purge). So the pod
  the hint started was gone by Enter: first call warm 0/3 in every typing cell. The hint
  only added idle pod-seconds (airline lookahead 1: median 201.7 vs 121.9 with no hint;
  coding: 332.0 vs 235.0).
- **The Enter-time hint did not help either.** The LLM thinks for well under a second, and
  a start takes about 1.6-2.9 s.
- **What helped was the normal prediction after each call.** The first call of turn 2 found
  a warm pod on Skypiea in every cell, 3/3 (airline reservation_lookup 33 ms vs about
  3,000 ms on the baseline; coding run_tests about 40 ms vs 3,100-4,200 ms). Turn 3 was warm
  3/3 only in airline with lookahead 1 (1/3 in one lookahead-2 cell); the coding `shell` call
  was always cold. The agent's
  median total waiting time per conversation with lookahead 1 was 4.4-4.5 s vs 15.4-17.1 s
  (airline) and 9.5-10.3 s vs 13.3-14.3 s (coding).
- **Lookahead 2 is mixed and was not adopted.** In the simulator it brought no gain at equal
  cost (support: identical, because all airline classes share one server; coding: +3.02
  vs +3.56 points over keep-alive on VAL, more idle). In the console runs it was worse for
  airline (the handover was cold 0/3 instead of warm 3/3; waiting 8.5 s vs 4.4 s). For
  coding it waited less (7.4 s vs 9.6 s) at more idle (270.8 vs 235.0 pod-s).
- **Skypiea cost more in every one of the 12 cells**: 4-11x the baseline's idle
  pod-seconds. Say it plainly: the waiting time it saves is bought with idle pods.
- **LLM behaviour:** in codefix 9 calls per cell were written as text (the console recovered
  and flagged them); in airline 3 calls per cell failed on a stack (invalid LLM arguments).
- **Not measured:** early connect (implemented, off by default, `platform.gateway.earlyConnect`).
  E0 bounds its possible saving at the readiness lag, median 0.5-0.8 s per tool
  (`docs/handoff/LEADTIME_REPORT.md`). No live human-typed conversation was recorded for the
  grid.
- **After the grid (v1.2, in progress):** the typing hint is re-sent at Enter. In 5 smoke
  conversations with Skye the first call was warm on Skypiea (31-38 ms vs 1,470-2,413 ms on
  the baseline). Smoke runs, n = 5, not a measurement; source:
  `docs/handoff/PHASE_A_HANDOFF.md` on branch `feat/console-support` (**not on `main`** unless
  v1.2 is merged).

### 4.9 Live Console verification runs (demo runs, 2026-11-06)

Source: `results/live_console_demo/runs.md`, caveats in `results/live_console_demo/README.md`
and `docs/handoff/LIVE_CONSOLE_REPORT.md`. One conversation per run, llama3.1:8b, kind.

| run | agent waited on tools, baseline / Skypiea ms | cold calls baseline / Skypiea | idle pod-s baseline / Skypiea |
|---|---|---|---|
| support_hint_off | 7,548 / 5,311 | 3 / 2 | 17.9 / 36.7 |
| support_hint_on | 6,322 / 3,576 | 3 / 2 | 18.6 / 50.5 |
| coding_hint_off | 8,096 / 6,461 | 3 / 2 | 21.1 / 92.4 |
| coding_hint_on | 7,913 / 4,240 | 3 / 2 | 20.1 / 99.2 |
| support_predictor_killed | 5,002 / 4,239 | 2 / 2 | 8.3 / 11.1 |

- With the hint on, the first call on Skypiea was started by the hint and waited less than with
  it off (support 1,856 vs 2,920 ms; `runs.md`), but it was still cold: the LLM thought for
  well under a second.
- Where Skypiea was clearly faster, the pod was warm from an **earlier** decision
  (transfer_to_human 32 ms vs 2,504 ms in support_hint_off; `runs.md`).
- **Predictor killed:** every call that succeeded on the baseline also succeeded on Skypiea.
- These runs used the first baseline lane (fixed afterwards in c4c8e9c); in 4 calls the
  baseline gap was longer than Skypiea's (listed in `runs.md`). One run per row: do not
  compare rows as effects.

## 5. What we do NOT claim (from `docs/limits.md`)

- **No production cost or latency claims.** Cold starts come from one 2-vCPU m7i-flex.large.
  Pricing is an assumption (`configs/model/pricing.yaml`).
- **No claim that we beat keep-alive on real runs.** The AWS runs are 20 sessions per policy and
  the CIs overlap. Idle pod-seconds there is one counter with no CI.
- **No shared-pool results.** In the simulator each session has its own pods and is its own
  node. Real deployments share pods, which would help keep-alive and always-warm (and Scenario
  C) more than our numbers show. **Our simulator may flatter prediction relative to
  keep-alive.** The scale-up cap is applied per session, so real contention is higher.
- **The 2-at-once start time is borrowed** from the 3-at-once measurement. E0 never measured
  exactly 2. The cap of 2 is a setting (the vCPU count), not a measured optimum.
- **tau2 is small:** 28 VAL and 56 TEST sessions with timing (342 TEST calls). The support
  profile rests on tau2 alone. tau-bench has no timestamps, so it gives accuracy numbers only.
- **The `prd` profile and every SYNTHETIC row are synthetic.** They show the mechanism runs on
  the PRD scenarios and say nothing about real workloads. Scenario C is our interpretation.
- **The shipped coding profile is not a blind TEST result.** It was adopted after its TEST
  margins were seen; the frozen rows stay next to it.
- **Other tuning disclosures:** support and frozen coding were tuned on kind cold starts. There
  was one earlier look at tau2 TEST (2026-11-03). VAL also served the ranker's early stopping.
- **The idle budget is our reading of NFR-3**, not the PRD's exact wording.
- **The load test was on the laptop, service alone**, not end-to-end on AWS.
- **The simulator does not model the 150 ms hold.**
- **712 of 734,021 TraceLab calls have no service time** and are treated as 0 ms. This affects
  at most 0.23% of TEST calls. The counts come from an audit, not from a results file.
- **No independent review.** The planned fresh-eyes review was skipped for time.
- **Every kind run is a demo run** (live agent, Live Console, lead-time grid): one laptop, a
  local 8B model, 1 to 10 conversations per cell, cached images. Skypiea idled more than the
  baseline in every console run. The typing speed of the scripted customer (200 characters
  per minute) is an assumption.
- **The console's airline data is a mock database inside the console**, and the 8B model can
  claim actions it did not take (v1.2 smoke run 2, `docs/handoff/PHASE_A_HANDOFF.md` on
  `feat/console-support`). The tool containers and their cold starts are real.
- **Not measured by us:** NFR-4's missed-prediction rate of at most 1%
  (`docs/numbers_and_sources.md`), NFR-2 at 100 calls/s end-to-end, and 500 real concurrent
  sessions on the cluster (**UNSOURCED** as results: no committed file).

---

## 6. "Why this number" table

Rows marked **UNSOURCED** are design choices or assumptions with no requirement or measurement
behind them.

| Number | What | Source | One-line reason |
|---|---|---|---|
| 50 ms | FR-1: top-3 within 50 ms; NFR-1: at most 50 ms added to p99 at 500 sessions | PRD, `docs/prd_excerpts.md`; `docs/numbers_and_sources.md` | Prediction must cost far less than the 2.7 s+ cold start it hides. Measured service p99 is 13.2 / 15.9 ms (`results/loadtest.csv`), laptop, service only. |
| 40 ms | Per-rung budget in the fail-open ladder | `docs/model.md` "Warm decisions" | Leaves headroom under the 50 ms target. Exact choice of 40 is **UNSOURCED**. |
| tau 0.6 | Threshold at which FR-2 is judged; Markov-1 baseline's tau | PRD FR-2; `configs/model/sim.yaml` `markov_tau` | The PRD fixes it for the acceptance test, so baselines are compared at it. |
| tau 0.4 | Support profile's threshold | `configs/model/profiles.yaml`; `results/sim_choice_support.json` | VAL pick under the 30% idle budget, which binds for support. It is not the PRD's 0.6. |
| lambda 100 | Coding profile's cost threshold (pod-s worth paying per second of cold start saved) | `configs/model/profiles.yaml`; `results/sim_choice_coding_cap2.json` | VAL pick, but at the end of the sweep (1, 3, 10, 30, 100), so it is not a well-located optimum. |
| 30 s TTL | Default lifetime of an unused warm pod | PRD FR-3; `charts/skypiea/values.yaml` `defaultTtlS: 30` | The PRD default. Also the keep-alive 30 s baseline and coding's keep-after-use. |
| TTL + 5 s | Pod removed within TTL + 5 s of last use | PRD FR-3; `values.yaml` `purgeS: 5` | FR-3 acceptance bound. |
| 150 ms hold | Gateway holds a call for a pod that is already starting | PRD FR-9; `gateway/main.py` `SKYPIEA_HOLD_MS` | Avoids racing a second cold start against one already under way. Not modelled in the simulator. |
| cap of 2 | At most 2 pods starting at once per node | `charts/skypiea/values.yaml` `maxScaleUpsPerNode`; `docs/gateway_controller.md` | Equals the node's 2 vCPUs. 3 at once doubled cold starts (2.7 to 5.5 s). **A setting, not a measurement**: 2 at once was never measured. |
| 3 / 20 pods | Max replicas per server / in total | `configs/model/decide.yaml`; `docs/gateway_controller.md` | **UNSOURCED** design choice. Sized to fit one 2 vCPU / 8 GiB node (`docs/infra.md`). |
| 500 sessions | Concurrency in NFR-1, NFR-4, Scenario C | PRD, `docs/prd_excerpts.md` | The PRD's scale target. Not run on the cluster. The simulator does not run 500 sessions on one shared pool. |
| 75 req/s ~ 500 sessions | Load-test rate that stands for 500 sessions | `services/predictor/loadtest.py` line 8 | **UNSOURCED**: assumes one call per ~6.7 s per session. NFR-4's 8 calls per 30 min would be about 2 req/s. |
| 30% idle budget | Tuning constraint: idle pod-s at most 30% of always-warm's | `configs/model/sim.yaml` `nfr3_idle_ratio: 0.3` | Our reading of NFR-3's 30%. The PRD counts never-used CPU-s against total CPU-s; both are reported. |
| 2.7 s / 5.4 s | AWS cold start, one pod alone, cached / uncached: median of all E0 runs pooled over tools (2,734 / 5,360 ms, 160 runs each; the median of the per-tool medians is 2,732 / 5,197 ms) | `results/e0_aws_runs.csv`, `results/e0_aws_summary.csv`; `docs/numbers_and_sources.md` | What prediction has to hide. One 2-vCPU node. |
| 5.5 s / 9.0 s | AWS cold start, 3 at once, cached / uncached: median of all runs pooled over tools (5,541 / 9,020 ms, 180 runs each) | `results/e0_aws_p3_runs.csv`, `results/e0_aws_p3_summary.csv` | CPU contention on 2 vCPUs. Reason for the cap. |
| 2,572 to 8,366 ms | Range of per-tool AWS medians (cached minimum, uncached maximum) | `README.md` E0 table | Light tools are fast; heavy tools (scikit-learn) are slow. |
| 49.1% to 15.5% cold | Scenario A, scale-to-zero vs predictive, AWS | `results/scenario_aws_summary.csv` | Main real-cluster result vs scale-to-zero. CIs do not overlap. |
| 58.9% to 5.6% cold | Scenario B, scale-to-zero vs predictive, AWS | same | Same. |
| +3.4 to +18.2 points | Equal-idle margin over keep-alive, shipped profiles, AWS cap 2 | `results/sim_sensitivity.csv.gz` | Simulator, hundreds of TEST sessions. The coding margin is the modest claim; support is the strong one. |
| 61.4% warm | Support on tau2 (cached, AWS + cap) at 11,859 idle pod-s / 1k | `results/sim_sensitivity.csv.gz` | Hit rate vs keep-alive 30 s at 56.4% / 18,218. |
| 67.6% warm | Coding (shipped) on TraceLab (cached, AWS + cap) at 27,387 / 1k | same | Hit rate vs keep-alive 30 s at 56.9% / 17,622. |
| 86.8% / 90.9% | FR-2: predicted calls Ready on arrival, support, tau 0.4 | `results/sim_offline.csv.gz` | Above the 80% bar, but at tau 0.4 and on 76 / 44 calls. |
| 0.529 top-1 | Ranker on TraceLab TEST (n-gram 0.454) | `results/p1_accuracy.csv.gz` | Why LightGBM: it beats the graph on coding traces. It does not beat it on tau2. |
| 20 sessions | Per policy in each AWS scenario run | `docs/results_summary.md` | Time and budget limit. This is why the CIs are wide. **UNSOURCED** as a design choice. |
| 1,000 resamples, seed 0 | Session bootstrap for the AWS CIs | `docs/results_summary.md` | Standard; resampling sessions respects within-session correlation. |
| 28 / 56 | tau2 VAL / TEST sessions with timing | `docs/limits.md` | Small, so a few sessions move tau2 numbers by several points. |
| lookahead 1 | Steps ahead the predictor warms (shipped) | `results/sim_lookahead_val.md`, `results/sim_lookahead_test.md`; `docs/handoff/LEADTIME_REPORT.md` | Lookahead 2 was not adopted under the pre-registered rule: no gain at equal idle in the simulator, and worse for airline in the console grid. |
| 200 chars/min | Typing speed of the scripted console customer | `results/live_console_leadtime/tables.md` header | **An assumption, not a measurement.** It makes the first message take about 39-43 s, longer than TTL + purge (35 s), which is why the typing hint expired. |
| 4-11x | Skypiea's idle pod-s over the baseline's in the 12 lead-time cells | `results/live_console_leadtime/tables.md` | Demo runs, n = 3 per cell. The waiting saved is paid with idle pods. |
| $0.108/h | AWS node cost while running | `README.md` "AWS"; details in `docs/aws_plan.md` | Why one small node. |

---

## 7. Design decisions and why

### Our own controller instead of OpenFaaS

- OpenFaaS Community Edition cannot scale to zero (`docs/gateway_controller.md`,
  `controller/README.md`).
- OpenFaaS Pro has scale-to-zero, but it is **reactive** (it scales down after idleness and
  scales up on demand), it does not take predictions, and it is proprietary.
  **Verify the current OpenFaaS docs wording before the viva**; this point is not backed by a
  committed file.
- We needed things no off-the-shelf autoscaler gave us together: a warm request with a
  future start time (`start_in_ms`) and a TTL, a per-node limit on concurrent starts with a
  due-time queue, the 150 ms hold, and an idle pod-seconds metric. A small kopf operator on
  plain Deployments does this in one cluster hop.
- We used HTTP rather than a CRD for warm requests because they are useful only for a few
  seconds. A CRD write needs the API server, an etcd commit and a watch event. The operator
  is restart-safe: it rebuilds its state from Deployment annotations
  (`docs/gateway_controller.md`, "Why HTTP and not a CRD").
- Knative/KEDA: **UNSOURCED**, as no committed file discusses them. If asked: they are
  reactive autoscalers driven by request or queue metrics. Prediction would still need a layer
  like ours on top.

### Markov graph + LightGBM re-ranker

- The PRD's own predictor is a graph prior blended with P(next | current tool), i.e. first-order
  Markov, with higher-order Markov as its first extension (`docs/prd_excerpts.md`, "Predictor").
  We built that, plus n-grams.
- The LightGBM re-ranker adds features the graph cannot use (timings, batch structure, recent
  history). It raises TraceLab top-1 from 0.454 (n-gram) to 0.529, and Copilot from 0.490 to
  0.506 (`results/p1_accuracy.csv.gz`).
- It is cheap: ranker p99 of about 2 to 6.4 ms on the real sources (`docs/results_summary.md`, calibration
  table). It is tabular, small, CPU-only, and uses metadata only, never prompts.
- It is fail-open: ranker, then n-gram, then majority, then none. A slow or broken rung is
  skipped (`docs/model.md`).
- Honest caveat: it does not help on tau2 (0.520 vs 0.620 for n-gram and Markov-1).
- Raw scores are shipped. Isotonic calibration made TEST ECE slightly worse on the real sources
  and never changes the ranking (`docs/model.md`, "Calibration").

### kind locally + k3s on one EC2 node

- kind: free, fast to rebuild, runs the same Helm chart. Used for functional checks only;
  timings from kind are a lower bound because images come from a local registry.
- k3s on one m7i-flex.large: a real cloud network path to ECR and a real 2-vCPU node, at about
  $0.108/h, with no inbound ports (SSM only) (`README.md`, `docs/aws_plan.md`).
- One node keeps cold-start measurements interpretable: no cross-node scheduling noise.
- Why not EKS: more cost and setup for a course project, and a managed control plane adds
  nothing to a cold-start measurement on one node. **UNSOURCED** (no committed comparison).

### No shared pool in the simulator

- Each session has its own pods and is its own node (`docs/model.md`, "Offline simulation";
  `docs/limits.md`). Reasons: it keeps sessions independent, so session-level bootstrap and
  per-session metrics are clean, and it ran within the time we had. The shared-pool ablation
  (Poisson arrivals, concurrency 1/10/100/500) was planned but not built (`docs/model.md`).
- Cost of this choice, said openly: sharing would help keep-alive and always-warm more than
  prediction, so **the simulator may flatter prediction relative to keep-alive**
  (`docs/limits.md`). Note that `docs/model.md` calls per-session mode "the conservative case";
  `docs/limits.md` is the later and more careful statement. Quote limits.md.
- The AWS runs *do* share pods across 20 sessions, and there predictive is on the keep-alive
  curve rather than above it. That fits the limit above.

---

## 8. 40 likely examiner questions

**Problem and motivation**

1. *What problem are you solving?* Cold starts of scale-to-zero tool containers called by AI
   agents. We hide the start inside the LLM's think time by predicting the next tool.
2. *Why not just keep everything warm?* Cost. On AWS, always-warm idled 6,534 pod-s in A and
   14,675 in B, vs 633 and 2,807 for predictive (`results/scenario_aws_summary.csv`).
3. *How long is a cold start?* About 2.7 s cached and 5.4 s uncached on one pod alone; up to
   8.4 s for heavy tools uncached; about double when 3 start at once (`results/e0_aws*`).
4. *Is there enough think time to hide it?* Often. 91.1% of TraceLab calls and 78.1% of tau2
   calls have a think gap at least as long as the cached AWS cold start; uncached it is 67.8%
   and 56.1% (`results/sim_hideable.csv`).
5. *Do you look at prompts?* No. We use tool names, timings and batch structure only. The
   predictor keeps no session state on disk (NFR-6, `tools_audit/audit_no_persistence.py`).

**Results**

6. *What is your headline result?* On real AWS runs, cold calls drop from 49% to 16% (support)
   and 59% to 6% (coding) vs scale-to-zero, with non-overlapping CIs, at a tenth to a fifth of
   always-warm's idle.
7. ***Why doesn't it always beat keep-alive?*** Keep-alive is a strong baseline when tools are
   reused within the TTL: it gets those calls warm for free, with no prediction. Prediction
   gains on *first uses* and *long gaps*, and pays for wrong guesses. The balance depends on
   the workload. On support traces (many first uses) the margin is large (+15.7 / +18.2). On
   coding it is modest (+3.4 to +5.1). On the 20-session AWS runs we are on the keep-alive
   curve with overlapping CIs. Our earlier frozen coding profile was *below* keep-alive on
   TraceLab under the cap (-1.9 / -2.4).
8. *Then why use it rather than keep-alive?* It has a dial (tau or lambda) that moves along the
   cost-latency curve, and on support-like traffic it is above the curve in simulation. On
   coding-like traffic, a tuned keep-alive is a reasonable choice, and we say so.
9. *Are your CIs meaningful with 20 sessions?* They are honest about the uncertainty: wide, and
   overlapping vs keep-alive. They are session-level bootstraps (1,000 resamples), so
   within-session correlation is respected.
10. *Why no CI on idle pod-seconds?* Pods are shared by all sessions in a run, so idle time
    cannot be split per session. It is one counter per run.
11. *Why are A and B compared with different keep-alives?* We ran keep-alive 10, 30 and 60 s
    for both. Predictive happens to sit near 10 s in A and near 60 s in B; that is "on the
    curve".
12. *Simulator and AWS disagree. Which do you believe?* Both, for what each is. The simulator
    has hundreds of sessions but no shared pool (it may flatter prediction). AWS is real but
    has only 20 sessions. Together they say: clear wins over scale-to-zero and always-warm,
    and a regime-dependent result vs keep-alive.
13. *What is the oracle for?* It knows every future call, so it bounds what prediction could
    achieve. Cap-aware, cached: TraceLab 72.5% warm at 1,269 idle pod-s per 1k, vs our 67.6%
    at 27,387. The gap is mostly idle cost, i.e. headroom for better prediction.
14. *Why does the oracle warm fewer calls than you with uncached images?* It starts each pod
    just in time and never keeps one idle. When a start is longer than the think gap, it
    cannot hide it, while our policy keeps pods longer. It bounds idle cost, not warm rate.
15. ***Is the PRD scenario model synthetic?*** Yes. The `prd` profile is trained and tested on
    traces sampled from the PRD's own scenario graphs, and Scenario C is our interpretation.
    It shows the mechanism runs on the PRD scenarios and is not evidence about real
    workloads. In SYNTHETIC B, predictive was colder than keep-alive 30 s (26.2% vs 21.2%).
    The real scenarios use tau2 (support) and TraceLab (coding).
16. *How accurate is next-tool prediction?* Top-1 0.529 on TraceLab (n-gram 0.454), 0.506 on
    Copilot, 0.618 on tau-bench; top-3 is 0.84 to 0.91 (`results/p1_accuracy.csv.gz`).
17. *Does the ML model help everywhere?* No. On tau2 the ranker is worse than Markov-1 (0.520
    vs 0.620), with only 56 TEST sessions.
18. *Did you tune on the test set?* The selection was on VAL. Two disclosures: one look at tau2
    TEST on 2026-11-03 during a bug hunt, with no choice made from it; and the shipped coding
    profile was adopted after its TEST margins were seen. The frozen rows are kept
    (`docs/limits.md`).
19. *What is the Markov-1 † row?* An overnight run where the old stub rounded probabilities
    and some answers were rejected. We reran with a floored stub; the numbers barely changed
    (`README.md`).

**Numbers and settings**

20. ***Where does 50 ms come from?*** The PRD: FR-1 (top-3 within 50 ms) and NFR-1 (at most
    50 ms added to p99 at 500 sessions) (`docs/prd_excerpts.md`). We measured 13.2 / 15.9 ms
    p99 at 75 / 150 req/s for the service alone on a laptop. The predictor is also off the
    critical path: it is called after the reply is sent.
21. *Then is NFR-1 met?* For the service alone, yes. End-to-end at 500 sessions on AWS was not
    measured. We do not claim it.
22. *Why tau 0.4 and not the PRD's 0.6?* 0.4 was picked on VAL under the 30% idle budget. FR-2
    is judged at the PRD's 0.6 for the baselines. We report FR-2 for support at its own tau
    (86.8% / 90.9%).
23. *What is lambda 100?* The coding profile warms a tool when expected savings beat expected
    waste: p >= (cold + ttl) / (cold + ttl + lambda x cold). 100 was the VAL pick but is the
    end of the sweep, so it is not a located optimum.
24. *Why a 30 s TTL?* It is the PRD FR-3 default, and it is the keep-alive baseline.
25. *Why a 150 ms hold?* PRD FR-9's default. It stops a second cold start racing a pod that is
    already starting. The simulator does not model it.
26. *Why at most 2 scale-ups at once?* 3 pods starting together on 2 vCPUs doubled cold starts
    (2.7 to 5.5 s cached) because Python start-up is CPU-bound. 2 equals the vCPU count. It is
    a setting; we never measured exactly 2 at once.
27. *Did the cap change your conclusions?* For the frozen coding profile, yes: it went from
    slightly above keep-alive to below it on TraceLab. That is why coding was retuned to one
    copy per tool. Support was unaffected (`results/sim_sensitivity.csv.gz`, cap table).
28. *What is the 30% idle budget?* Our reading of NFR-3: idle pod-s at most 30% of
    always-warm's. The PRD counts never-used CPU-s against always-warm's total CPU-s. Both are
    reported. Our shipped profiles are at 1.2 to 23.6% in our reading and at most 1.5% in the
    PRD's (`results/sim_sensitivity.csv.gz`).
29. *Did you test 500 sessions?* Not on the cluster. The load test used 75 req/s as a stand-in
    for 500 sessions, and that equivalence is an assumption.

**Design**

30. *Why not OpenFaaS?* CE cannot scale to zero. Pro's scale-to-zero is reactive and
    proprietary, and we needed predictive warm requests with a start time, a TTL, and a
    per-node start cap. (Verify the current OpenFaaS wording before the viva.)
31. *Why not Knative or KEDA?* They scale on observed demand. We would still need a predictive
    layer on top, and our operator is small and does exactly what we measure. (No committed
    comparison: **UNSOURCED**.)
32. *Why HTTP to the operator instead of a CRD?* Warm requests live for seconds. A CRD adds an
    API-server write, an etcd commit and a watch event. The operator rebuilds its state from
    Deployment annotations after a restart.
33. *What happens if the predictor dies?* The call is unaffected and the system becomes plain
    scale-to-zero (FR-4). Tested by scaling the predictor to 0 on kind.
34. *What if the operator crashes mid-scale?* Replicas and annotations go in one PATCH. The new
    operator rebuilt `run_tests` with its remaining TTL and made no duplicate scale (live test,
    `docs/gateway_controller.md`).
35. *Why one EC2 node?* Cost (~$0.108/h), interpretability of the cold starts, and no inbound
    ports. The limit is that the cold starts come from one small node.
36. *Why does the gateway route to pod IPs, not the Service?* kube-proxy needs 0.1 to 0.7 s
    after Ready to program a new pod. On kind, every cold call failed with "connection
    refused" before this change.
37. *Why no shared pool in the simulator?* Time, and clean per-session statistics. It may
    flatter prediction vs keep-alive; we state this in `docs/limits.md`.
38. *How do you avoid leaking data between sessions?* Each session has its own history in the
    gateway. Ended sessions are purged within 10 s and idle ones after 600 s. The predictor
    persists nothing, and an audit script checks it.

**Reflection**

39. ***What would you do with more time?*** (a) The shared-pool simulator and a 500-session
    run on the cluster. (b) More AWS sessions per policy, so the keep-alive comparison has
    non-overlapping CIs or a clear tie. (c) Measure exactly 2 starts at once. (d) Extend the
    lambda sweep past 100. (e) A per-source profile, and a hybrid of keep-alive and prediction.
    (f) Hash session ids in the gateway (a known limitation: ids with `:` disable prediction).
    (g) An independent review. (h) End-to-end NFR-1 and NFR-2 measurements on AWS.
40. *What is the one thing you would want us to remember?* Prediction clearly beats
    scale-to-zero and always-warm. Against a tuned keep-alive it is workload-dependent, and we
    have shown exactly where it wins and where it does not, with every number traceable to a
    committed file.

Extra hard ones, in case:

- *Is the Live Console real?* The tool containers are real pods and cold-start for real on
  kind (laptop timings, lower than AWS). The airline data is a mock database inside the
  console; the pods receive only the tool class with empty arguments. The LLM is a local 8B
  model (`README.md`, "Live Console").
- *Your agent said it changed the flight. Did it?* Not necessarily: the 8B model can claim
  actions it did not take (seen in a v1.2 smoke run). The tools refuse invalid changes; the
  truth panel shows the session database's real state.
- *Why did the typing hint not help?* The scripted first message took about 39-43 s to type,
  longer than the 30 s TTL plus the 5 s purge, so the hinted pod expired before Enter: first
  call warm 0/3 in every typing cell (`results/live_console_leadtime/tables.md`). v1.2 re-sends
  the hint at Enter (smoke runs only).
- *In the console, Skypiea always idles more. Isn't that a loss?* It is the price: 4-11x the
  baseline's idle pod-seconds in the lead-time grid, for less waiting on tools. The console
  runs are demo runs; the cost-latency comparison against keep-alive is in section 4.2-4.3.
- *Why not lookahead 2?* Pre-registered rule: adopt only with a gain at equal idle on VAL. It
  had none (support identical, coding +3.02 vs +3.56 points; `results/sim_lookahead_val.md`).

- *Did AI write this?* We used AI coding assistants for parts of the code and docs. The team
  reviewed, tested, and owns all of it (`README.md`).
- *The session-id bug?* The gateway forwards client session ids unchanged. Ids outside
  `^[A-Za-z0-9_.-]{1,64}$` make the predictor return no decisions, so those sessions fall back
  to scale-to-zero. The real-timing runs were unaffected because the replayer sends uuid4 hex
  ids (`docs/gateway_controller.md`).

---

## 9. 10-slide outline

1. **Skypiea: predictive prewarming for agentic tool calls**
   - Team, DS252, IISc; 6 Nov 2026
   - One line: start the next tool's pod while the LLM is thinking
   - Gateway + operator + predictor + CLI; kind and k3s on AWS
2. **The problem: cold starts vs think time**
   - Scale-to-zero is cheap but cold: 2.6 to 8.4 s per tool on AWS (`results/e0_aws*`)
   - Always-warm is fast but idles: 6,534 / 14,675 idle pod-s in A / B
   - Think gaps cover the cold start for 56 to 91% of calls (`results/sim_hideable.csv`)
3. **Architecture**
   - One MCP endpoint; 150 ms hold; predictor off the critical path
   - kopf operator: 0..N, at most 2 starts per node, TTL 30 s + 5 s
   - Fail-open: a predictor failure means plain scale-to-zero
4. **The predictor**
   - Markov/n-gram graph (the PRD's model) + LightGBM re-ranker, metadata only
   - Top-1 0.529 vs 0.454 on TraceLab; worse on tau2 (said openly)
   - Warm policy: tau or cost threshold lambda, copies, timing; 30% idle budget
5. **E0: measuring cold starts**
   - kind (lower bound) vs AWS m7i-flex.large, n = 10 per tool
   - 3 at once doubles the start (2.7 to 5.5 s): CPU-bound start-up
   - Hence the cap of 2 (a setting, not a measurement)
6. **Real AWS runs**
   - Table: cold % with CIs and idle pod-s, A and B
   - vs scale-to-zero: 49 to 16%, 59 to 6%; vs always-warm: a tenth to a fifth of idle
   - vs keep-alive: on the curve; CIs overlap
7. **Simulator: equal-idle margins**
   - +15.7 / +18.2 (support, tau2); +3.4 to +5.1 (coding)
   - Frozen coding was -1.9 under the cap, which is why it was retuned (disclosed)
   - Oracle headroom: mostly idle cost
8. **Live demo**
   - `make live`: Live Console, Normal (scale-to-zero) vs Skypiea side by side, local 8B LLM
   - Real pods, real cold starts on kind; mock airline database; the model can over-claim
   - Kill the predictor: calls still succeed (fail-open)
   - Backup: `make demo` / `make agent-demo`, `docs/live_backup/`
9. **Limits: what we do not claim**
   - One small node; 20 AWS sessions; no shared pool in the simulator
   - tau2 small; prd profile SYNTHETIC; coding adopted after TEST
   - No production cost claims; no independent review
10. **Conclusion and future work**
    - Clear wins over scale-to-zero and always-warm; regime-dependent vs keep-alive
    - Next: shared-pool simulator, 500 sessions on the cluster, more AWS sessions
    - Every number traceable: `docs/numbers_and_sources.md`
