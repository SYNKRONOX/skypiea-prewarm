# Predictive Prewarming for Agentic Tool Calls over MCP on Kubernetes/OpenFaaS

**DS252 Cloud Computing: Project Requirements Document (original plan)**

> This file is the team's original PRD, version v1.1 dated 18 September 2026, transcribed from the submitted PDF. The text is unchanged except that personal e-mail addresses were removed, and Figure 1 (a drawing in the PDF) is described in words rather than reproduced.
>
> This is the plan as written. Where the implementation differs (for example, our own controller on plain Kubernetes Deployments instead of OpenFaaS), see [`docs/PRD_STATUS.md`](docs/PRD_STATUS.md), which lists the status of every requirement with links to the evidence. The as-built architecture is described in [`dev.md`](dev.md).

| Field | Value |
|---|---|
| Team name | Skypiea |
| Team members | Gorripati Parthav Chandraditya (SR# 25302) · Lukalapu Mohnish (SR# 25451) · Chodisetty Dhanunjaya (SR# 25415) |
| Summary (≤25 words) | Predicts an agent's next MCP tool call and pre-provisions its container ahead of need, cutting cold-start latency at bounded warm-pool cost. |
| Date | 18 September 2026 |
| Version | v1.1 |

---

## 1. Problem Statement and Motivation

Enterprises increasingly deploy LLM-based agents — customer-support copilots, coding assistants, operations bots — that accomplish tasks by invoking external capabilities (databases, search, code execution, ticketing systems) through standardised tool-calling protocols such as the Model Context Protocol (MCP). End users experience the agent's total response time as the sum of every tool call in its reasoning chain; platform teams bear the infrastructure cost of keeping those tools ready to respond.

Tool servers are commonly deployed as serverless functions (OpenFaaS/Kubernetes, AWS Lambda, Knative) that scale to zero when idle, since keeping every tool always warm is prohibitively expensive at scale. The technical gap is that a scale-to-zero function pays a cold-start tax on its next invocation — image pull, runtime initialisation, MCP session/handshake setup — typically hundreds of milliseconds to several seconds. A single-step web request tolerates this; a multi-step agent workflow that sequentially invokes five to fifteen tools compounds it into multi-second stalls that are highly visible to end users.

This predictability is not just intuition. AutoTool's macro-level analysis of the ToolBench corpus — 16,000+ real-world REST APIs across 49 categories, with solution paths built via a depth-first-search decision tree [Qin et al., 2023] — measured a conditional entropy of only 3.62 bits over ~16,000 real tool-call trajectories, against a theoretical maximum of 10.64 bits for the 1,595 tools involved: a two-thirds reduction from what a random walk would produce [Jia and Li, 2025]. Once an agent has just called one tool, which tool it calls next is already far from a coin flip. On the serving side, the very recent SpecBox system acts on similar predictability directly from the LLM's own token stream, reporting up to a 2.9× cut in P99 latency and a 45.9% reduction in peak memory versus permanently-reserved sandboxes [Zhang et al., 2026]. These results are why we treat prewarming as worth building rather than a hunch — just not yet from the workflow-graph-and-history angle this project targets, which is lighter-weight than SpecBox's token-stream interception and better suited to a six-week build.

The problem is inherently a distributed-systems and cloud-scheduling one, not one solvable on a single machine: it needs (a) cluster-wide visibility into pod lifecycle and warm-pool state across many co-located tool functions, (b) elastic, event-driven container orchestration to provision and evict pods ahead of and after need, and (c) multi-tenant handling of many concurrent agent sessions sharing the same infrastructure.

## 2. Scope

### In scope

- A prediction engine combining a static workflow-graph prior with a historical first-order Markov transition model (Sec. 6 explains the model in detail).
- A Kubernetes warm-pool controller that provisions predicted tool pods ahead of invocation, evicts unused ones via TTL, and queues near-miss requests briefly rather than racing a second cold start (FR-9).
- Integration with OpenFaaS running a representative set of 6–8 MCP tool functions on a single cluster.
- A configurable synthetic workload generator replaying agent tool-call traces (deterministic, branching, stochastic).
- An evaluation harness comparing scale-to-zero, always-warm, and predictive-prewarming configurations.

### Out of scope

- Production-grade security hardening of tool sandboxes beyond namespace-level isolation.
- Multi-cluster or multi-region prewarming; the system targets a single Kubernetes cluster.
- Support for agent frameworks other than LangGraph, and for MCP tools other than the representative set built for evaluation.
- Automatic retraining pipelines beyond the manually triggered model reload in FR-6.

### Assumptions

- The agent's workflow graph structure is available to the prediction engine (a LangGraph state machine, not a fully dynamic/unstructured agent).
- Logged historical traces are representative of the traffic used at evaluation time; no adversarial drift is assumed for the MVP.
- Tool container images are pre-built and available in a registry reachable from within the cluster, so cold start is dominated by pull + init, not build time.
- The evaluation cluster provides isolated CPU/memory quotas so warm-pool cost measurements are not confounded by co-tenant noise.
- Tool pods run in standard Linux containers (namespaces/cgroups), not a stronger sandbox such as gVisor or Firecracker; NFR-3's cost figures should be read as a lower bound on what a hardened production deployment would actually spend.

## 3. Stakeholders and Usage Scenarios

### Roles

- **Agent Developer** — defines the LangGraph workflow and consumes prediction hints; does not manage infrastructure directly.
- **Platform/SRE Operator** — configures warm-pool policy (confidence threshold, TTL, pool size caps) and monitors the latency/cost trade-off.
- **End User** — interacts with the agent and experiences the resulting end-to-end latency; has no visibility into the prewarming mechanism.

### Usage scenarios

- **Scenario A — Customer-support refund ticket.** A user reports a damaged item; the agent's LangGraph workflow moves through classify_intent → lookup_order (calls `order_lookup` against the orders database) → check_policy (calls `policy_check`, which evaluates the return window and item category) → issue_refund (calls `refund_issue` against the payment gateway) → notify_user. In our synthetic model this exact three-tool chain covers roughly 85% of tickets in this category, making it a near-deterministic testbed for the static graph-prior half of the predictor (FR-1) and for FR-2's before-the-call provisioning.
- **Scenario B — Coding-assistant PR review.** The agent moves through `read_file` → `run_tests` → `format_diff` → `post_comment`, a fixed linear pipeline with no branching. This scenario isolates the graph-prior signal from the learned Markov model — if prewarming keeps pace here using graph structure alone, that is a useful ablation confirming FR-1 does not depend entirely on historical data.
- **Scenario C — Demand spike on a rarely used tool.** A marketing campaign drives concurrent sessions from a handful up to 500 within a few minutes (modelled directly in our soak test, NFR-4), most of them calling a `promo_code_lookup` tool that has sat scaled to zero for days because it normally sees almost no traffic. The warm-pool controller must scale prewarmed capacity for that one tool without a thundering-herd resource blowout — directly exercising NFR-3's overhead bound and NFR-4's stability bound.

## 4. Functional Requirements

Each requirement below states the testable behaviour, its acceptance criterion, and — because every numeric bar in this PRD should be defensible rather than asserted — the reasoning behind the specific number chosen.

| ID | Requirement and acceptance criterion | Why this number |
|---|---|---|
| **FR-1** (Must) | Return a ranked list of likely next tools with confidence scores for an active session, given the current workflow node and call history. **Acceptance:** top-3 predictions within ≤50 ms; confidences sum to ≤1. | 50 ms keeps the predictor's overhead below a single LLM decode step (tens of ms/token on common inference stacks), so it never becomes the new bottleneck. Top-3 covers the branching factor at any node in our scenario graphs (rarely more than 2–3 valid next tools). |
| **FR-2** (Must) | Provision a pod for the predicted tool before the real invocation arrives, once confidence exceeds threshold τ. **Acceptance:** for τ = 0.6, the pod is Ready before the request arrives in ≥80% of replayed trajectories. | τ = 0.6 is a starting default above the 50% majority line, biasing precision over recall; Sec. 7's confidence-threshold sweep is the experiment that justifies or revises it. 80% leaves the remaining near-misses to FR-9's queue, so 100% is not the right bar here. |
| **FR-3** (Must) | Evict a warm pod that stays unused past a configurable TTL (default 30 s). **Acceptance:** pod terminated within TTL + 5 s of last use. | 30 s approximates how long a Scenario A/B session takes to finish its remaining calls, so a correctly-prewarmed pod isn't evicted before use. +5 s absorbs the controller's own reconciliation cycle (NFR-5) and metrics-scrape latency. |
| **FR-4** (Should) | Fall back to standard OpenFaaS scale-from-zero when confidence stays below τ. **Acceptance:** forced no-prediction test completes via the cold path, no error surfaced. | No new numeric threshold — reuses τ from FR-2. This is a correctness/availability requirement, not a performance one. |
| **FR-5** (Must) | Expose per-call cold/warm latency and warm-pool occupancy as Prometheus metrics. **Acceptance:** endpoint returns cold/warm-labelled histograms, graphable in Grafana. | No numeric SLA of its own — it exists so every NFR in Sec. 5 has an instrument to measure it with, instead of being asserted on faith. |
| **FR-6** (Should) | Reload the historical transition model from newly logged traces without dropping in-flight predictions. **Acceptance:** reload completes in <2 s with no in-flight errors — validated in Experiment E5, Sec. 7. | 2 s stays well below the average inter-call gap in our synthetic sessions (5–10 s), so a reload is invisible relative to a live session's own pace rather than a visible stall. |
| **FR-7** (Could) | Track concurrent sessions' trajectories independently, without cross-session leakage. **Acceptance:** two divergent concurrent sessions yield independent predictions in a two-session test. | Qualitative correctness requirement — no numeric threshold to justify. |
| **FR-8** (Must) | Replay synthetic agent traces at a configurable request rate and speed multiplier (1×, 5×, 10× — see Sec. 7 for exactly what this means). **Acceptance:** replayed inter-call timing is within 5% of the target at each speed. | 5% is a standard tolerance for user-space trace-replay timing (scheduler/timer jitter, e.g. asyncio sleep drift) — tight enough to trust the generator without needing real-time OS guarantees this project doesn't need. |
| **FR-9** (Should) | If a predicted pod is still provisioning when the real call arrives, hold the request in a short bounded queue (default cap 150 ms) instead of racing a second cold start. **Acceptance:** served by the already-warming pod in ≥90% of forced race trials; queue wait never exceeds the cap. | 150 ms approximates how long a partly-underway MCP handshake + container init typically needs to finish — far less than a fresh cold start (seconds), so shaping is always a net win. 90% accepts a residual minority that falls through to FR-4. |

## 5. Non-Functional Requirements

| ID | Target, workload and measurement method | Why this number |
|---|---|---|
| **NFR-1** | Prediction engine adds ≤50 ms to p99 end-to-end tool-call latency at 500 concurrent sessions, sustained 10-min steady state, measured with Locust from 3 client VMs. | Mirrors FR-1's decode-step argument. 500 sessions approximates a mid-size enterprise support desk — large enough to stress a small lab cluster meaningfully without needing cloud-scale hardware. |
| **NFR-2** | p50 tool-invocation latency within 20% of the always-warm baseline at a sustained 100 tool-calls/second, measured end-to-end at the client. | 20% is chosen because human turn-taking perception in chat is roughly 100–200 ms; a 20% gap on typical few-hundred-ms calls stays under that threshold. 100 calls/sec is what our workload generator (FR-8) can sustain continuously. |
| **NFR-3** | Warm-pool overhead (CPU-seconds on pods never used) ≤30% of the always-warm baseline's total CPU-seconds, same workload. | Back-of-envelope for our 6–8 tool set: if on average only 2–3 tools (25–35%) are genuinely 'hot' per trajectory, a good predictor should approach that floor rather than always-warm's 100%. |
| **NFR-4** | 500 concurrent sessions (avg. 8 calls/session) for 30 minutes, no controller crash, missed-prediction rate ≤1%. | 30 minutes spans several TTL cycles (FR-3's 30 s default) many times over, exercising steady-state churn, not just start-up. 1% mirrors a loose operational target — a miss only triggers FR-4's fallback, never a user-visible error. |
| **NFR-5** | Controller reconciliation loop completes within 200 ms/cycle at a cluster scale of 50 nodes / 200 pods, measured via controller-runtime metrics. | 200 ms keeps the controller's own loop comfortably inside FR-9's 150 ms shaping cap plus network margin, so it is never the dominant source of the delay it exists to hide. |
| **NFR-6** (Security/privacy) | Per-session trajectory data is not persisted beyond the session and is purged within 60 s of session termination, verified by an automated storage audit script. | 60 s is comfortably larger than a typical session's own duration (15–40 s in Scenario A/B), so data is never purged mid-session, while keeping the exposure window to roughly a minute. |

## 6. System Architecture and Technology Stack

Figure 1 in the original PDF shows the component diagram, tier placement, and the data crossing each tier boundary. The client application sits in the device tier and issues agent queries over HTTPS into the cloud tier. All remaining components — the LangGraph agent runtime, the prediction engine, the warm-pool controller, and the OpenFaaS tool functions — are co-located within a single Kubernetes cluster (cloud tier); prediction and prewarm/evict signals never leave the cluster. A cold path additionally crosses to the container registry when a tool image must be pulled.

*(Figure 1 — component diagram and tier placement — is a drawing in the original PDF and is not reproduced here. The as-built architecture is in [`dev.md`](dev.md).)*

### How the prediction engine works: a first-order Markov model over tools

The predictor treats each MCP tool as a state in a small state machine, plus two virtual states, *start* and *end*, marking a session's beginning and completion. From historical or replayed sessions we count which tool immediately followed which, and estimate a transition probability P(next = j | current = i) as the count of i→j transitions divided by the total number of times tool i was followed by anything. This is first-order: it looks only at the current tool, not the full history before it, which keeps the lookup a single table read — essential for staying inside FR-1's 50 ms budget. Table 1 illustrates the idea with two rows drawn from Scenario A.

| From tool | To tool | P(next) |
|---|---|---|
| order_lookup | policy_check | 0.70 |
| order_lookup | refund_issue | 0.20 |
| order_lookup | end | 0.10 |
| policy_check | refund_issue | 0.55 |
| policy_check | end | 0.45 |

*Table 1. A worked example of learned transition probabilities out of two tools in Scenario A.*

Not every mathematically possible transition is actually valid — the workflow graph forbids some edges — so we zero out transitions the graph doesn't allow, then blend the rest with the learned frequencies: final_score(j) = α·graph_prior(j) + (1−α)·P(j | current tool), with graph_prior(j) = 1 for an allowed edge and 0 otherwise (renormalised), and α tuned during Weeks 3–4. A higher-order model (conditioning on the last k tools) is known to reduce uncertainty further — AutoTool (Sec. 1) reports lower entropy moving from first to second order — but we keep first-order for the MVP since a k-th order table grows combinatorially and risks breaking FR-1's 50 ms budget; it is the first extension we'd try if the MVP finishes early.

### Frameworks and managed services, with justification

- **OpenFaaS on Kubernetes for tool serving** — self-hostable, giving direct control over pod placement unlike AWS Lambda's provisioned-concurrency black box, and lighter to instrument in 6 weeks than Knative's CRD layer.
- **A custom Kubernetes controller (client-go/kopf)** reacting to prediction events in near real time rather than periodic polling.
- **Prometheus/Grafana for observability** — both integrate natively with Kubernetes and OpenFaaS metrics endpoints.

### Design decisions and rejected alternatives

- **Decision:** no edge tier; all components run in one cloud-hosted cluster. **Rejected:** splitting tool servers to an edge tier for locality. **Reason:** prewarming decisions are latency-sensitive at the ~100 ms scale (NFR-1); an added cross-tier hop would dominate the very savings being measured, and the workload has no device-proximity requirement to justify it.
- **Decision:** lightweight graph-prior + first-order Markov predictor for the MVP. **Rejected:** a transformer-based sequence predictor. **Reason:** inference-latency risk against FR-1's 50 ms budget, and insufficient training-trace volume achievable in six weeks.
- **Decision:** event-driven custom controller over a cron-based/polling prewarmer. **Rejected:** a fixed-interval polling loop. **Reason:** FR-2's deadline-aware provisioning needs sub-second reaction to prediction events, which polling at any practical interval cannot guarantee.
- **Decision:** build our own warm-pool controller rather than adopt Fission's poolmgr executor, which already keeps a generic pool of pre-initialised pods and 'specialises' one into a function in well under a second. **Rejected:** switching the FaaS layer to Fission. **Reason:** Fission's specialize step would absorb the exact reconciliation-and-eviction problem (FR-2, FR-3, NFR-5) this project studies, leaving little of our own contribution beyond the prediction signal; we accept the larger build risk to keep the warm-pool logic as the thing we measure, with a fast-polling fallback (Sec. 8, Risk 2) if the event-driven version isn't ready by Wk 4.
- **Decision:** predict and provision as two discrete steps at workflow-node transitions, rather than reading the agent's live token stream the way SpecBox does. **Rejected:** token-level speculative routing. **Reason:** it needs access to the LLM's raw output plus a second real-time classifier — a reasonable next step, but more than a 6-week build can evaluate soundly; recorded as a stretch item in Sec. 8.

## 7. Evaluation Plan

### What the workload generator actually does

The generator's job is to turn a written-down agent session — which tool was called, from which workflow node, and how long after the session started — into real HTTP/MCP calls fired at the cluster at the right moments. Each synthetic session is stored as a small JSON record; the listing below shows a representative one for the Scenario A refund flow.

```json
{
  "session_id": "sess_0091",
  "trace_type": "branching",
  "events": [
    {"t_offset_ms": 0,    "node": "classify_intent", "tool_called": null},
    {"t_offset_ms": 120,  "node": "lookup_order",    "tool_called": "order_lookup"},
    {"t_offset_ms": 640,  "node": "check_policy",    "tool_called": "policy_check"},
    {"t_offset_ms": 1310, "node": "issue_refund",    "tool_called": "refund_issue"},
    {"t_offset_ms": 1890, "node": "notify_user",     "tool_called": null}
  ]
}
```

The generator reads a batch of such session files and, for each one, schedules a call to the corresponding tool endpoint at t_offset_ms / speed_multiplier after that session's own start time. Session start times are drawn from a Poisson process, so the arrival rate of new sessions — and hence the aggregate calls-per-second the cluster sees — is a separate knob from the speed multiplier. The three trace types differ only in how the event list is produced: *deterministic* uses a fixed list as stored (Scenario B); *branching* uses the same format but samples which valid next node to take at each decision point, via fixed probabilities in its own graph definition (Scenario A); *stochastic* chooses the next tool via a random walk over the raw tool-adjacency graph, ignoring the workflow's preferred edges, to stress the Markov model's worst case.

**What '1× / 5× / 10×' means, precisely:** a load-testing knob, not network throttling — no artificial delay is added anywhere. Every t_offset_ms in a stored session is divided by the multiplier before scheduling: at 1× the generator reproduces the original pacing; at 5× the same calls fire five times faster (a 2 s session now spans 400 ms). Because session start times keep arriving at their normal Poisson rate while each session's own calls compress, the aggregate calls-per-second rises roughly in proportion to the multiplier — this is how load increases without a separate load-generation mechanism. It is not the same as adding latency or dropping packets on the network path; that would be a link-degradation experiment, out of scope here (Sec. 2).

Because LangGraph's built-in checkpointer performs synchronous state persistence at every node transition, its I/O time would otherwise be counted as part of the tool-call latency we are trying to measure. The evaluation harness therefore runs with an in-memory (or async) checkpointer during timed runs, so NFR-1 and NFR-2 isolate prewarming's effect from LangGraph's own bookkeeping cost.

### Baselines and cost model

Baseline configurations: (a) scale-to-zero / no prewarming (OpenFaaS default), (b) always-warm (every tool pod kept hot), (c) predictive prewarming (this project). Cost model: dollars per 1,000 tool invocations = warm-pod compute cost (CPU-seconds consumed, used or wasted, × a reference on-demand vCPU-second price) + cold-start compute overhead incurred, normalised per 1,000 calls.

### Experiments: what we run, why, and what each one validates

| Exp. | What we do and why | Validates |
|---|---|---|
| **E1** | Replay all three trace types at 1×/5×/10× across all three configurations. This is the core experiment: it compares our system against the cheap-but-slow and fast-but-expensive extremes, across traces of increasing unpredictability, showing not just whether prewarming helps but where its benefit shrinks as structure disappears. | FR-1, FR-2, FR-8, NFR-1, NFR-2 |
| **E2** | A 30-minute soak at 500 concurrent sessions (avg. 8 calls/session), including a dedicated two-session isolation check. A stability/correctness experiment, not a latency one: does the system keep working — no crash, no cross-session leakage, an acceptable miss rate — once TTL eviction, pool churn, and reconciliation are all exercising each other at once. | FR-7, NFR-4, NFR-5 |
| **E3** | Three targeted correctness tests: TTL eviction fires on time; a low-confidence session still succeeds via the cold path; a real call deliberately timed to land mid-provision exercises FR-9's queue. These probe the edge behaviour where a naive implementation is most likely to silently break, rather than measuring performance. | FR-3, FR-4, FR-9 |
| **E4** | Compute the cost model across all configurations and trace types and plot latency against cost. This is where the project's central claim is made or broken: that predictive prewarming sits on a better latency/cost trade-off point than either extreme, not merely that it is 'fast' or 'cheap' in isolation. | NFR-3 (NFR-6 via storage audit) |
| **E5** | Trigger a live model reload while a session is mid-flight with an outstanding prediction, and confirm the reload finishes inside the 2 s budget with that request still served correctly. A narrow test of whether updating the model is safe to do live — not whether the updated model is more accurate, which would need a longer-running experiment outside this project's window. | FR-6 |

**Expected results:** (i) a latency-vs-cost Pareto plot with the three configurations as points, showing predictive prewarming closer to always-warm's latency at a fraction of its cost; (ii) CDFs of tool-invocation latency per configuration; (iii) a prediction accuracy vs. confidence-threshold curve, which is also where FR-2's τ = 0.6 default gets empirically justified or revised; (iv) a resource-overhead bar chart (CPU-seconds wasted per configuration); (v) a pass/fail correctness record for E3 and E5, which are edge-case tests rather than plots.

## 8. Work Plan, Milestones and Risks

### Task breakdown (6 weeks)

| Week | Tasks | Owner |
|---|---|---|
| 1 | Cluster + OpenFaaS setup; deploy 6–8 representative MCP tool functions; measure baseline cold vs. always-warm latency. | Parthav |
| 2 | Build workload generator and trace format (FR-8); define synthetic trace types. | Mohnish |
| 3 | Implement graph-prior + Markov predictor (FR-1); unit tests. | Parthav |
| 4 | Build warm-pool controller with provisioning, TTL eviction, and request shaping (FR-2, FR-3, FR-4, FR-9). | Mohnish |
| 5 | Integrate Prometheus/Grafana (FR-5); implement session isolation (FR-7); prepare the hot-reload path (FR-6) for Experiment E5. | Dhanunjaya |
| 6 | Run full evaluation matrix (E1–E5); red-team tests; write-up and viva preparation. | All |

### Milestones (demonstrable deliverables; periodic spot checks apply)

- **M1 (end Wk 2):** baseline cold/always-warm latency numbers and a working workload-generator demo replaying a saved trace.
- **M2 (end Wk 4):** end-to-end predictive prewarming demo on the deterministic trace — a warm pod visibly provisioned ahead of the actual tool call.
- **M3 (end Wk 6):** full evaluation results (latency-vs-cost Pareto plot, accuracy curve) and red-team findings presented.

### Principal risks and mitigations

- **Risk:** prediction accuracy too low to yield a latency benefit. **Mitigation:** FR-4's fallback to the graph-prior-only signal remains demonstrable even if the learned model underperforms.
- **Risk:** Kubernetes controller complexity underestimated for a 6-week timeline. **Mitigation:** build on an existing controller framework (kopf/client-go samples) rather than from scratch; descope to a fast-polling reconciler if event-driven reconciliation isn't ready by Wk 4.
- **Risk:** shared lab cluster resource contention skews warm-pool cost measurements. **Mitigation:** run in an isolated namespace with resource quotas, or fall back to a local kind cluster with a documented cost model.

### MVP vs. stretch goals

**MVP:** FR-1, FR-2, FR-3, FR-4, FR-5, FR-8 and NFR-1, NFR-2, NFR-4, evaluated with E1 and E3 on the deterministic trace at one load level (100 sessions), comparing scale-to-zero vs. predictive prewarming only.

**Stretch goals:** the full factorial evaluation matrix (all trace types × all load levels × all three configurations), multi-session isolation (FR-7), and the hot model reload (FR-6, tested by Experiment E5) — run only if the MVP is complete and time remains.

## 9. References

- Model Context Protocol specification, https://modelcontextprotocol.io (accessed Sep 2026).
- OpenFaaS documentation, https://docs.openfaas.com.
- Kubernetes client-go and controller-runtime documentation, https://kubernetes.io/docs.
- LangGraph documentation, https://langchain-ai.github.io/langgraph.
- Zhang, Y. et al. (2026). SpecBox: Speculative Sandbox Scheduling for Efficient LLM Agent Serving. arXiv:2607.23933.
- Jia, J. and Li, Q. (2025). AutoTool: Efficient Tool Selection for Large Language Model Agents. arXiv:2511.14650.
- Qin, Y. et al. (2023). ToolLLM: Facilitating Large Language Models to Master 16000+ Real-World APIs (ToolBench dataset). arXiv:2307.16789.
- Fission poolmgr documentation, https://fission.io/docs; KEDA and the KEDA HTTP Add-on, https://keda.sh/docs.
- Microsoft Azure Public Dataset — Azure Functions traces, https://github.com/Azure/AzurePublicDataset.
- Anthropic Claude (LLM) — used to iterate on scoping, stress-test the evaluation design, verify cited claims, and critique the architecture.
