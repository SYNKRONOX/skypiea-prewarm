# Limits, in plain language

These are the things our results do not show, or show only under assumptions. Each limit
applies to every number in `docs/results_summary.md`.

**Each session gets its own pods.** In the simulator a pod belongs to one (session, server)
pair, and no session ever uses another session's pod. A real deployment runs one Deployment
per tool server, shared by every session. Sharing would help keep-alive and always-warm, and
Scenario C (500 sessions calling one tool), more than our numbers show. Our results may
therefore flatter prediction compared with keep-alive.

**There is no shared pool, and each session is its own node.** For the same reason, the scale-up
cap is applied per session: other sessions never take the 2 scale-up slots. On a real node all
sessions compete for them, so real queueing and contention are higher than simulated.

**The timing for 2 starts at once is borrowed from the 3-at-once measurement.** E0 measured
1 pod starting alone and 3 starting together, never exactly 2. The simulator gives any start
that overlaps another the 3-at-once time, which overstates the slow-down for 2. It also does
not slow a start that is already running when a later one begins, which understates it. The
cap of 2 itself is a setting (the node's vCPU count), not a measured optimum.

**tau2 is small.** Only 28 VAL and 56 TEST sessions have timing (342 TEST calls). A few
sessions can move the tau2 numbers by several points, and the support profile rests on tau2
alone. The plan's "n=15" does not match the data.

**tau-bench cannot be replayed.** None of its 1,928 sessions has timestamps, so it gives
accuracy numbers but no warm-pool numbers at all.

**The prd profile is synthetic.** It is trained and tested on traces sampled from the PRD's
own scenario graphs. Scenario C in it is our interpretation (one promo_code_lookup call per
session; the PRD gives no graph). It shows that the mechanism runs on the PRD scenarios, and
it says nothing about real workloads.

**The shipped coding profile was adopted after we had seen the TEST margins.** The retune
(one copy per tool) was selected on VAL only. It used a new selection rule, the equal-idle
margin over keep-alive, because the original rule never binds for coding. The decision to
ship it came after the TEST margins were known, so its TEST result is not a blind test. The
frozen profile's results are kept next to it in every table.

**Other tuning disclosures.** The support and the frozen coding profiles were tuned on the kind
cold starts, not AWS. There was one earlier TEST look at tau2 (2026-11-03, disclosed in
`docs/model.md`). VAL also served the ranker's early stopping.

**The idle budget is our reading of NFR-3.** We tuned with idle pod-seconds <= 30% of
always-warm's idle. The PRD's NFR-3 counts CPU-seconds of pods never used against
always-warm's total CPU-seconds. Both ratios are in the sim rows, but the tuning used ours.

**Cold starts come from one small node.** All AWS cold starts were measured on one 2-vCPU
m7i-flex.large. The pricing is an assumption (`configs/model/pricing.yaml`), so there are no
production cost or latency claims. The load-test latencies were measured on the laptop for the
service alone, not end-to-end.

**The AWS scenario runs are small.** Scenarios A and B each replay 20 sessions per policy
(the count is printed in `docs/results_summary.md`), so the cold-rate CIs are wide and
overlap for several policies. Idle pod-seconds there is one counter for the whole run, with
pods shared by all 20 sessions, so it has no session-level CI.

**Some TraceLab calls have no recorded run time.** 712 of 734,021 TraceLab calls (395 of
225,290 in TEST) have no `service_ms`. They are kept and treated as taking 0 ms: a batch ends
at its slowest known call. This makes the next think gap about 0.1 s too long (median 87 ms
in TEST, using the train median per tool) for at most 0.23% of TEST calls; with that
imputation 2 to 4 TEST calls would cross a cold-start threshold. No timestamp is missing.
These counts come from an audit run on 2026-11-05 (`tests/test_nan_timing.py` checks the
guard). They are not in a results file.

**The simulator leaves things out.** It does not model the gateway's 150 ms hold (FR-9), and
the AWS scenario runs from the infra branch are not in our results yet (TODO rows in
`docs/results_summary.md`).

**The fresh-eyes review was skipped.** The planned Phase 11 review by someone who had not
built the system was skipped for time. Nobody independent has checked these results.
