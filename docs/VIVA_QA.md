# Viva: the 25 hardest questions, answered honestly

Each answer names the committed file its numbers come from. Where no committed file backs a
point, the answer says so. The broader prep (pitch, demo script, 40 likely questions) is in
[`VIVA.md`](VIVA.md); what we did not claim is in [`limits.md`](limits.md).

## Design choices

**1. Why not OpenFaaS, as the PRD planned?** OpenFaaS Community Edition cannot scale to zero
(`docs/gateway_controller.md`, "Controller"; `docs/PRD_STATUS.md`, OpenFaaS row: Changed). We
also needed what no autoscaler gave us together: a warm request with a future start time
(`start_in_ms`) and a TTL, a per-node cap on concurrent starts with a due-time queue, and an
idle pod-seconds counter. What OpenFaaS Pro offers today is **not** backed by a committed file;
we say that rather than guess.

**2. Why write your own controller instead of using Knative or KEDA?** They scale on
observed demand; prediction would still need a layer like ours on top. Ours is a small kopf
operator on plain Deployments (`controller/`), restart-safe through Deployment annotations
(`docs/gateway_controller.md`). Honest gap: no committed file compares Knative or KEDA, so we
cannot claim ours is faster than them.

**3. Isn't a 150 ms hold cap broken if `held_ms` in the AWS runs goes above 150?** The AWS
runs (`results/scenario_aws_calls.csv`, recorded before W1) stored the time from arrival to
resume under the name `held_ms`: 675 held calls, 541 above 150.5 ms, overrun above 150 ms p50
1.0 ms, p99 24.0 ms, max 247.2 ms (`docs/PRD_STATUS.md`, FR-9; `python -m gateway.hold_report`).
The wait itself is bounded by the cap (`tests/test_gateway_hold.py`); the extra is the event
loop resuming the call late, and it was always included in `added_ms`. Since W1 the gateway
reports `held_ms` (the hold, <= 150 ms), `hold_late_ms` and `hold_wait_ms` separately. The
PRD's ">= 90% served by that pod in forced races" was **not measured**: FR-9 is Partly met.

## Data and evaluation

**4. Your support data has LLM-simulated users. Is that real?** No, and we say so. tau2-bench
users are LLM-simulated and its tool times come from a simulated run (README "Training data",
`data/README.md`). TraceLab is the source with real coding-agent timings. The `prd` profile is
fully SYNTHETIC (sampled from the PRD's own graphs) and is used only by `make demo`.

**5. The coding policy was tuned on Copilot too. Isn't Copilot then not a fair test?** Right:
the coding profile's ranker is trained on TraceLab TRAIN, but its warm policy was tuned on
TraceLab **and** Copilot VAL (README "Training data"). So the Copilot TEST margins (+5.1 / +5.0
under the cap, `results/sim_sensitivity.csv.gz`) are in-distribution for the policy, not
transfer. The only transfer number is E1's TraceLab → Copilot row: ranker 0.458 vs Markov-1
0.428 top-1 (`results/p1_accuracy.csv.gz`).

**6. How do you know there is no train/test leakage?** Splits are by group with seed 0 (30%
of groups to TEST, then 20% of the rest to VAL), so no group is in two splits
(`docs/training_data.md`, `data/README.md` step 5). `tests/test_no_leakage.py` replaces every
event from position t on with junk and checks that every registered model and feature builder
gives identical outputs at t, and that a deliberately leaky model is caught. E1 also reports
group k-fold (GKF) columns. Disclosed exceptions: one look at tau2 TEST on 2026-11-03 with no
choice made from it, VAL also used for the ranker's early stopping, and the shipped coding
profile was adopted after its TEST margins were seen (`docs/limits.md`).

**7. tau2 is tiny. Can you conclude anything from it?** Little. 56 TEST sessions (342 calls),
28 VAL, 99 TRAIN (README "Training data"). E1 CIs are wide: Markov-1 0.620 [0.49, 0.73]. On it
the ranker is *worse* than Markov-1 (0.520 [0.44, 0.58]), and we report that. The simulator
margin for support (+15.7 / +18.2 under the cap) rests on these 56 sessions alone.

**8. The predictive and keep-alive CIs overlap. So you didn't beat keep-alive?** Correct, on
AWS we did not show it. Scenario A: predictive 15.5% [10.2, 21.8] cold vs keep-alive 10 s
15.5% [9.7, 23.2]; Scenario B: 5.6% [2.3, 10.6] vs keep-alive 60 s 6.6% [3.1, 11.8]
(`results/scenario_aws_summary.csv`). With 20 sessions per policy we are "on the keep-alive
curve". What we did show: versus scale-to-zero the CIs do not overlap (49.1% → 15.5% in A,
58.9% → 5.6% in B), and versus always-warm idle drops from 6,534 to 633 pod-s (A).

**9. Then where does prediction win?** In the simulator at equal idle: +3.4 to +18.2 points
over keep-alive under the AWS cap (README "Simulator", `results/sim_sensitivity.csv.gz`).
The simulator gives each session its own pods and node, which may flatter prediction
(`docs/limits.md`), and the frozen coding profile was *below* keep-alive on TraceLab under
the cap (-1.9 / -2.4).

**10. Why only 20 sessions per policy on AWS?** No committed file records the reason; the
run notes record the setup: 20 sessions per scenario, 5 at once, unscaled gaps, and the same
session draw (`scenario_sessions.json`) reused on the second night (`docs/handoff/MORNING_REPORT.md`,
`docs/handoff/OVERNIGHT_REPORT.md`). We list it as a limitation (`docs/limits.md`). More
sessions per policy is the first thing we would run next (question 25).

**11. Is idle pod-seconds a fair cost measure?** It is what we can measure for every policy
in the same way (`skypiea_pod_seconds_idle_total`). It is one counter per run with no CI,
because pods are shared by sessions. Dollar costs use assumed prices
(`configs/model/pricing.yaml`); NFR-3 on AWS is idle pod-s (predictive 9.7% / 19.1% of
always-warm), not the PRD's CPU-seconds (`docs/PRD_STATUS.md`, NFR-3).

## Requirements not met

**12. NFR-4 (500 sessions x 8 calls for 30 min) is not done. Why?** We ran 40 concurrent
TraceLab sessions on AWS instead: 938 of 938 calls succeeded, never more than 2 starting; the
harness crashed 3 times first on NaN timings (`results/concurrent40_aws.csv`,
`docs/handoff/OVERNIGHT_REPORT.md`). No 500-session soak and no missed-prediction rate were
measured. NFR-5 (50 nodes) is not done either: we had one node.

**13. Is NFR-1 (<= 50 ms added at p99 at 500 sessions) met?** Only for the predictor service
alone on a laptop: p99 13.2 / 15.9 ms at 75 / 150 req/s (`results/loadtest.csv`). The mapping
75 req/s ≈ 500 sessions is an assumption (`docs/numbers_and_sources.md`, UNSOURCED). The
predictor is off the critical path: the gateway calls it after the reply.

**14. FR-6 says "reload from newly logged traces". Did you do that?** No. We reload a
prepared bundle under load: 0.49-0.50 s per reload, 0 non-200 of 3,754 requests
(`results/fr6_reload_check.json`), on a laptop, in one process. Retraining from logged traces
is not automatic.

## The live demo

**15. The Skypiea Air world: is any of it real?** Only the 46 airport codes, city names and
coordinates. Flights, fares (INR), 306 customers, 930 bookings and the demo-world date
(Monday 11 January 2027) are generated from a fixed seed by `agent/skyair/world.py`
(`docs/SKYPIEA_WORLD.md`; `python -m agent.skyair.world`).

**16. Skye passes 54/54. Is she 100% correct?** No. The suite (6 scenarios x 3 messy
variants, end-state oracle, `agent/suite/`) passed 54/54 for llama3.1:8b after the
guardrails, 22/36 before (`results/suite/after/summary.md`, `docs/SUITE_BEFORE_AFTER.md`). But
the guardrails were tuned while looking at llama's failures on this same suite; every request
runs at temperature 0 with a fixed seed, so the 3 reps are nearly identical; and before and
after used different worlds. It means every suite case passes, not that Skye is always right.
qwen3:8b got 46/54 and qwen2.5:7b 42/54.

**17. Your console shows Skypiea idling more every time. Isn't that a loss?** It is the
price. Skypiea idled more in every verification run (`results/live_console_demo/runs.md`) and
4-11x the baseline in the lead-time grid (`results/live_console_leadtime/tables.md`), for less
waiting on tools. These are demo runs (one conversation per row), not results.

**18. Did the turn-start hint help?** No. The typing hint expired before Enter (the first
message took about 39-43 s to type, longer than TTL 30 s + 5 s), so the first call was warm
0/3 in every typing cell; the Enter hint was too late for a 1.6-2.9 s start
(`results/live_console_leadtime/tables.md`, README "Lead-time grid").

**19. Is the console secure?** It is a local demo with no authentication, bound to 127.0.0.1
(`console/app.py`, `tests/test_console_security.py`). A known gap: it does not check the
`Origin` of requests, so a web page in the same browser could change settings
(`docs/handoff/REQUESTS_W2.md` R9; an expected-fail test shows it). `SECURITY.md` says how to
run it safely.

**20. Is the LLM's text checked?** Only by simple rules: "done" claims against the writes that
succeeded, and every price against tool results (`console/truth.py`, `docs/LIVE_DEMO.md`). The
model can still claim actions it did not take; the booking record shows the real state.

## Cost, scope, reflection

**21. What did AWS cost?** Prices, not a bill: one m7i-flex.large in ap-south-1 at about
$0.108 per hour ($2.60 per day) while running and about $0.06 per day stopped
(`docs/aws_plan.md`, "my arithmetic, not a bill"; `infra/aws/README.md`). No committed file
records the total we were billed, so we do not quote one.

**22. Cold starts from one 2-vCPU node: does that generalise?** No claim beyond it. Medians
2,572 to 8,366 ms per tool (`results/e0_aws_summary.csv`); starting 3 at once roughly doubles
them (`results/e0_aws_p3_summary.csv`), which is why the operator caps starts at 2 per node.
Exactly 2 at once was never measured; the simulator borrows the 3-at-once time.

**23. Who checked your numbers?** Nobody independent (README "Known limitations"). What we
did: every number names its source (`docs/numbers_and_sources.md`), `make check-claims`
recomputes the README and PRD_STATUS headline numbers from the committed result files, and
CI runs it with the tests. `docs/REVIEWER_GUIDE.md` is the shortest path for an outsider.

**24. What part did AI assistants play?** We used AI coding assistants for parts of the code
and documentation; the team reviewed, tested and owns it (README "Team").

**25. What would you do with another month?** (a) A shared-pool simulator and a 500-session
soak on the cluster (NFR-4). (b) More AWS sessions per policy, so the keep-alive comparison
either separates or is a clear tie. (c) Measure exactly 2 starts at once. (d) The FR-9 forced
race trial rate. (e) End-to-end NFR-1/NFR-2 on AWS. (f) A hybrid of keep-alive and prediction,
and the lambda sweep past 100 (`docs/VIVA.md`, question 39). (g) For the demo: a held-out
correctness suite written by someone who did not tune the guardrails, and fixing the
console's cross-site gap (R9).
