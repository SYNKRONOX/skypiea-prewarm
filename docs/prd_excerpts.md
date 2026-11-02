# PRD v1.1 excerpts and our interpretation

The PRD PDF itself is not committed (it contains personal contact details). This file holds
the verbatim requirement text the prediction engine is built and evaluated against, followed
by the definitions we use where the PRD leaves room. The second part is **our interpretation
of the PRD**, not PRD text.

## Verbatim excerpts

- **FR-1 (Must):** Return a ranked list of likely next tools with confidence scores for an
  active session, given the current workflow node and call history. Acceptance: top-3
  predictions within <=50 ms; confidences sum to <=1.
- **FR-2 (Must):** Provision a pod for the predicted tool before the real invocation arrives,
  once confidence exceeds threshold tau. Acceptance: for tau = 0.6, the pod is Ready before
  the request arrives in >=80% of replayed trajectories.
- **FR-3 (Must):** Evict a warm pod that stays unused past a configurable TTL (default 30 s).
  Acceptance: pod terminated within TTL + 5 s of last use.
- **FR-4 (Should):** Fall back to standard scale-from-zero when confidence stays below tau.
  Acceptance: forced no-prediction test completes via the cold path, no error surfaced.
- **FR-6 (Should):** Reload the historical transition model from newly logged traces without
  dropping in-flight predictions. Acceptance: reload completes in <2 s with no in-flight
  errors.
- **FR-7 (Could):** Track concurrent sessions' trajectories independently, without
  cross-session leakage.
- **FR-9 (Should):** If a predicted pod is still provisioning when the real call arrives, hold
  the request in a short bounded queue (default cap 150 ms) instead of racing a second cold
  start. Acceptance: served by the already-warming pod in >=90% of forced race trials.
- **NFR-1:** Prediction engine adds <=50 ms to p99 end-to-end tool-call latency at 500
  concurrent sessions.
- **NFR-2:** p50 tool-invocation latency within 20% of the always-warm baseline at a sustained
  100 tool-calls/second.
- **NFR-3:** Warm-pool overhead (CPU-seconds on pods never used) <=30% of the always-warm
  baseline's total CPU-seconds, same workload.
- **NFR-4:** 500 concurrent sessions (avg. 8 calls/session) for 30 minutes, no controller
  crash, missed-prediction rate <=1%.
- **NFR-6:** Per-session trajectory data is not persisted beyond the session and is purged
  within 60 s of session termination, verified by an automated storage audit script.
- **Cost model:** dollars per 1,000 tool invocations = warm-pod compute cost (CPU-seconds
  consumed, used or wasted, x a reference on-demand vCPU-second price) + cold-start compute
  overhead incurred, normalised per 1,000 calls.
- **Scenario A (customer-support refund):** classify_intent -> lookup_order (order_lookup) ->
  check_policy (policy_check) -> issue_refund (refund_issue) -> notify_user. About 85% of
  tickets follow the three-tool chain; branching: after order_lookup: policy_check 0.70,
  refund_issue 0.20, end 0.10; after policy_check: refund_issue 0.55, end 0.45.
- **Scenario B (coding PR review):** read_file -> run_tests -> format_diff -> post_comment, a
  fixed linear pipeline, no branching; isolates the graph-prior signal from the learned
  Markov model.
- **Scenario C (demand spike):** concurrent sessions rise from a handful to 500 within a few
  minutes, most calling promo_code_lookup, which has sat scaled to zero for days; the
  controller must scale prewarmed capacity for that one tool without a thundering-herd
  resource blowout (NFR-3 overhead bound, NFR-4 stability bound).
- **Predictor (PRD):** final_score(j) = alpha*graph_prior(j) + (1-alpha)*P(j | current tool);
  graph_prior(j) = 1 for an allowed edge, 0 otherwise (renormalised); higher-order Markov is
  the PRD's first extension.

## Our interpretation of the PRD

- **FR-2 (primary, per call):** among calls whose tool was predicted above tau early enough
  for its pod to be started, the share whose pod was Ready on arrival. Target >=80% at
  tau = 0.6. Also reported per trajectory: (a) trajectories where every such call was ready;
  (b) trajectories where at least 80% of such calls were ready.
- **FR-3:** a pod unused for TTL is removed within TTL + 5 s; checked in the simulator with a
  1 s reconciliation tick.
- **Cost:** prices and per-tool vCPU requests live in `configs/model/pricing.yaml` and are
  marked as assumptions until replaced by the on-demand price of our AWS instance type. No
  price is hard-coded.
- **Scenario C in the simulator:** concurrency ramps from 5 to 500 sessions over 3 minutes;
  all but 10% of sessions call promo_code_lookup, which starts at zero pods. We report peak
  pods, cold calls and wasted pod-seconds with and without the ramp limit.
- **Workflow nodes:** real traces have none. The graph prior uses the PRD scenario graphs
  (`configs/model/workflow_graphs.yaml`) on synthetic traces, and edges observed in train
  (min support 3) on real traces, labelled "learned edges, not workflow-defined".
