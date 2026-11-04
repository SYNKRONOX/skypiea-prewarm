# Gateway and scale-to-zero controller

```
agent / replayer --POST /mcp--> skypiea-gateway --forward--> tool Service --> tool pod (0..N)
                                    |   ^                                         ^
                       (after reply) |   | EndpointSlices (ready pods)            |
                                    v   |                                         |
                          skypiea-predictor (POST /decide) ---- decisions         |
                                    |                                             |
                                    +--POST /v1/warm, /v1/inflight--> skypiea-controller (kopf)
                                                                       patches replicas 0 <-> N
```

All three run from one image (`skypiea/platform`, `deploy/platform/Dockerfile`) and are
deployed with the tools by `helm install skypiea charts/skypiea` (`make deploy-kind`).
Every Service is ClusterIP: nothing is reachable from outside the cluster.

## Gateway (`gateway/`)

One MCP endpoint, `POST /mcp`, JSON-RPC `tools/call` (protocol 2026-07-28, stateless).

1. **Session**: `Mcp-Session-Id` header; created (and returned) if absent. Each session keeps
   its own history; sessions never see each other. `DELETE /mcp` ends a session; ended
   sessions are purged within 10 s and idle ones after 600 s (NFR-6: within 60 s of the end).
2. **Routing**: `params.name` is a tool class (`write_file`), a server name (`edit_file`) or
   anything else (-> fallback server, class `other`), mapped with `tools/tool_map.yaml`. The
   request is forwarded with `params.name` and the `Mcp-Name` / `Mcp-Method` headers rewritten
   to the server's tool (the 2026-07-28 servers check headers against the body).
   It goes **directly to a ready pod address** from the EndpointSlice (round-robin), not to the
   Service ClusterIP: the Service only routes to a new pod once kube-proxy has programmed it,
   0.1-0.7 s after Ready in E0, and on kind every cold call failed with "connection refused"
   before this change. Refused/reset connections are retried with backoff for up to 3 s
   (`skypiea_gateway_connect_retries_total`); the Service URL is the fallback.
3. **Hold (FR-9)**: if the server has no ready pod, the gateway asks the controller for one
   (`reason=demand`) and holds the call up to **150 ms** waiting for a pod that is about to be
   Ready (it watches EndpointSlices). If none, the call takes the **cold path** (waits for
   Ready, up to 120 s) and is marked cold (`X-Skypiea-Cold: 1`, metric label `cold="true"`).
   If no pod becomes Ready in 120 s (e.g. stuck Pending) the call fails with JSON-RPC
   error -32001; it never hangs.
4. **Predictor, off the critical path**: after the reply is sent, the gateway posts the
   session history to `/decide` (300 ms timeout) and forwards each `warm` decision to the
   controller, after `start_in_ms`. Contract: `docs/predictor_contract.md`.
5. **FR-4 fallback**: a dead, slow, non-JSON or off-contract predictor answer is counted and
   ignored; the call is unaffected and the system behaves as plain scale-to-zero.
6. **In-flight reports**: the gateway tells the controller how many calls are in flight per
   server (on change, and every 1 s as a heartbeat).

Metrics (`GET /metrics`): `skypiea_gateway_calls_total{tool,cold}`,
`skypiea_gateway_added_latency_seconds{tool}` (gateway time on top of the tool's own response:
hold + cold wait + proxying), `skypiea_gateway_hold_ms{tool}`,
`skypiea_gateway_prewarm_hits_total` / `_misses_total{tool}` (ready pod on arrival or not),
`skypiea_gateway_predictor_latency_seconds`, `skypiea_gateway_predictor_errors_total{reason}`,
`skypiea_gateway_controller_errors_total{op}`, `skypiea_gateway_not_ready_total{tool}`,
`skypiea_gateway_connect_retries_total{tool}`,
`skypiea_gateway_sessions`.

## Controller (`controller/`)

Our own operator ([kopf](https://kopf.readthedocs.io/), `kopf run --standalone --namespace
skypiea-tools -m controller.operator`) on plain Deployments (OpenFaaS CE cannot scale to zero).
It watches only Deployments labelled `skypiea.io/tier` (the tool servers) and runs a reconcile
loop every 250 ms around a pure, unit-tested `WarmPool` (`controller/core.py`).

### Why HTTP and not a CRD

Warm requests arrive after every tool call and are useful only for the next few seconds. A
`POST /v1/warm` to the controller takes one cluster-internal hop; a CRD would need a write to
the API server, an etcd commit and a watch event before the controller even sees it, adding
latency and API-server load for state that is short-lived by nature. Nothing is lost on a
restart: the controller rebuilds its state from the live Deployments (replica count plus the
`skypiea/last-used` and `skypiea/ttl-s` annotations that every scale patch writes, in the same
PATCH as the replicas), and the gateway's in-flight heartbeat repopulates in-flight counts
within a second.

### API (ClusterIP Service `skypiea-controller:8081`)

| Endpoint | Body | Effect |
|---|---|---|
| `POST /v1/warm` | `{tool_class, copies, ttl_s \| null, reason, start_in_ms?}` | Keep >= `copies` pods of that class's server until TTL after its last use. Returns `{server, accepted, requested}`. |
| `POST /v1/inflight` | `{gateway_id, counts: {server: n}}` | Calls in flight per server (stale after 30 s). |
| `GET /v1/state` | | Pool state per server (debugging, functional checks). |
| `GET /healthz` | | 503 until the pool has been rebuilt from the cluster. |
| `GET /metrics` | | See below. |

### Rules

- **TTL (FR-3)**: default 30 s when `ttl_s` is null; the pod is removed at TTL + **5 s** purge
  after its **last use** (the end of its last in-flight call, or the last warm request).
  Keep-alive baselines send their own T. A call that started a pod itself (`reason=demand`)
  uses `platform.gateway.demandTtlS` (0 = plain scale-to-zero).
- **Never below in-flight calls**: a server with calls in flight is never scaled down and has
  at least 1 replica. Pods serve many concurrent calls, so in-flight calls do not add replicas
  one-for-one.
- **No flapping**: at least `minDwellS` (5 s) between a replica change and a scale-down;
  scale-ups are never delayed.
- **Hard caps**: `maxPerTool` (3) replicas per server and `maxTotal` (20) across the cluster.
  Anything above is refused, counted in `skypiea_scale_refused_total{tool,reason}` and never
  applied. A later, smaller warm request never shrinks an active pool.
- **Idempotent**: each tick computes desired replicas and patches only where they differ from
  the live count; a failed patch is retried on the next tick.
- **Restart-safe**: replicas and annotations go in one PATCH, so a crash mid-scale leaves a
  consistent Deployment; on startup the pool is rebuilt before any scaling (a pod without
  annotations gets one default TTL, never an immediate scale-down). Strategy `Recreate` and
  1 replica: there is never more than one controller.
- **Stuck Pending**: replicas > 0 with no Ready pod for 120 s sets
  `skypiea_pending_stuck{tool}=1`; the gateway fails such calls after its cold timeout.
- **Always-warm baseline**: `platform.controller.alwaysWarm=true` keeps 1 replica per tool.
- **Concurrent scale-ups per node**: `maxScaleUpsPerNode` (default **2**; 0 = no limit),
  multiplied by `nodes` (default 1; the chart sets it because the controller has no
  cluster-wide RBAC to count nodes). At most that many pods may be *starting* at once
  (replicas set but not Ready). The details, and the measured E0 numbers behind them, are in
  the next section.

### Why at most 2 scale-ups at once per node (E0 on AWS)

E0 on the 2-vCPU m7i-flex.large (run `e0-20261104T142408Z-a24e540`, n=10 per tool and mode,
`results/e0_aws*_summary.csv`), median ms:

| | total | image_pull | app_init | total p95 |
|---|---|---|---|---|
| 1 pod at a time, cached | 2734 | 0 | 1211 | 3779 |
| 3 pods at once, cached | 5541 | 0 | 3672 | 6681 |
| 1 pod at a time, uncached | 5360 | 2723 | 1226 | 8400 |
| 3 pods at once, uncached | 9020 | 3464 | 3444 | 13186 |

- Starting three pods together roughly doubles every cold start, even with the image cached:
  2.7 s becomes 5.5 s. Almost all of the extra time is `app_init` (1.2 s becomes 3.7 s), which
  is CPU-bound Python start-up shared by three processes on 2 vCPUs.
- The same parallel-3 run on the 24-core laptop (kind) kept `app_init` at 0.74 s, so this is
  CPU contention on the node, not the code.
- With 3 at once, each of the 3 waits about 5.5 s. With a limit of 2, the first two take less
  than that and the third starts as soon as a slot frees.
- The limit equals the vCPU count. The node was not measured with exactly 2 at once. The
  value is a setting, and the queue metrics show when it bites.

Queue rules:
- Excess scale-ups wait in a queue ordered by when they are due: request time +
  `start_in_ms` (the gateway already delays a predictive warm by its `start_in_ms`, so
  arrival order is due order; the controller also accepts `start_in_ms` directly).
- On a tie, a server with a call already waiting goes first, then first come, first served.
- The queue holds pods, so a server that wants 2 more and gets 1 slot keeps 1 queued.
- **Nothing is dropped silently:**
  - every queued scale-up is counted in `skypiea_scaleups_queued_total{tool}`;
  - its wait is recorded in `skypiea_scaleup_queue_wait_seconds`, and the queue length in
    `skypiea_scaleup_queue_depth`;
  - a queued request whose warm TTL ends before it got a slot is counted in
    `skypiea_scaleups_expired_in_queue_total{tool}` and logged;
  - `GET /v1/state` shows `queued` per server.
- A pod stuck Pending for longer than 120 s (`skypiea_pending_stuck`) stops holding a slot,
  so one broken image cannot block every other tool.
- A call to a cold tool still waits in the gateway (up to its cold timeout, 120 s), so a
  queued demand scale-up delays the call but does not fail it.

Metrics: `skypiea_pod_seconds_idle_total{tool}` (Ready pod-seconds with no call in flight),
`skypiea_scale_events_total{tool,dir,reason}`, `skypiea_warm_pool_size{tool}`,
`skypiea_scale_refused_total{tool,reason}`, `skypiea_scale_errors_total{tool}`,
`skypiea_pending_stuck{tool}`, `skypiea_warm_requests_total{tool,reason}`,
`skypiea_scaleup_queue_depth`, `skypiea_scaleups_queued_total{tool}`,
`skypiea_scaleup_queue_wait_seconds`, `skypiea_scaleups_expired_in_queue_total{tool}`.

### RBAC (namespaced only)

| Service account | Rights in `skypiea-tools` only |
|---|---|
| `skypiea-controller` | deployments: get, list, watch, patch; deployments/scale: get, patch, update; events: create |
| `skypiea-gateway` | endpointslices: get, list, watch |
| predictor placeholder | no API token |

kopf's cluster-wide namespace/CRD scanning is disabled (`settings.scanning.disabled`), so no
ClusterRole is needed. Verified on kind with `kubectl auth can-i`: the controller cannot
delete Deployments or patch outside its namespace; the gateway cannot list pods.

## Policies (until the model branch is merged)

The predictor placeholder (`gateway/predictor_stub.py`) implements the baselines behind the
same `/decide` contract, selected with `platform.predictor.policy` or `POST /admin/policy`:
`scale_to_zero`, `always_warm`, `keep_alive` (T = 30 s), `markov1` (SpecBox-style first-order,
top-1, tau = 0.6, learned online), `predictive` (online n-gram stand-in, tau = 0.3), `oracle`
(the replayer's true next calls, started early by the tool's measured cached cold start from
`configs/cold_start.yaml`). They are stand-ins for functional checks; the real `decide()`
comes from `predictors/policies.py` after the merge.

## E0 and the platform

E0 scales tools itself, so it must run **without** the controller:
`make deploy-kind-e0` (or on AWS the default `PLATFORM=false`). E0 refuses to start while the
controller is running. `make deploy-kind` installs the full platform.

## Functional checks on kind (not results)

The laptop is shared with model training, so nothing below is a measurement; reported timings
come from the AWS run. `make kind-integration`, one Scenario A and one Scenario B session per
policy, think gaps x0.2: all calls succeeded under all six policies; `scale_to_zero` made every
call cold (held 150 ms, then cold path); `always_warm` made none cold; `keep_alive`, `markov1`,
`predictive` and `oracle` sent warm decisions that reached the controller; all tools returned
to 0 replicas afterwards. With x0.2 gaps (0.5-2.7 s) a correct prediction cannot beat a 2-4 s
kind cold start, so those calls were still cold; with the recorded gaps (x1.0) every
predicted call after the first one of a session found a warm pod under `markov1` and `oracle`.

Live failure injection on kind:

| Injected | Observed |
|---|---|
| predictor scaled to 0 | call succeeded (cold path), `predictor_errors_total` +1 |
| controller pod deleted right after scaling `run_tests` up (TTL 60 s) | new controller rebuilt from annotations: `run_tests` kept at 1 with 52 s left, no duplicate scale |
| tool pod unschedulable (nodeSelector) | call failed after 120 s with -32001 (no hang); `skypiea_pending_stuck{tool}` = 1 |

Operational note: `helm upgrade` (Helm 4, server-side apply) does **not** remove fields added
out of band with kubectl; revert manual edits explicitly.

## Reproduce

```bash
make build-images                 # tools, E0 probe, platform image (one at a time)
make cluster-kind deploy-kind     # kind + helm install skypiea (platform on)
pytest tests/test_gateway.py tests/test_gateway_mcp.py tests/test_controller.py
make kind-integration             # Scenario A + B through the gateway under each policy
```

`make kind-integration` reads one Scenario A and one Scenario B session from the synthetic
traces in the model worktree (read-only) and replays them with think gaps x0.2. On the shared
laptop its latencies are **functional checks only**; reported timings come from AWS.

## Known limitation: session id forwarded unchanged (2026-11-04)

The gateway forwards the client's `Mcp-Session-Id` to the predictor's `/decide` as
`session_id`, unchanged. The predictor (image aabdfd2a9a39 and later) degrades to
`mode=none` (no decisions) when the id is outside `^[A-Za-z0-9_.-]{1,64}$`: one `:`
is enough, as seen with TraceLab ids like `codex:…`. Such sessions get the cold path
(FR-4 fail-open: plain scale-to-zero), with no error. The real-timing runs are not
affected: the replayer sends a fresh uuid4 hex id per session. Planned fix (not done; no
gateway change during the runs): send an opaque hash of the id (contract proposal P2,
docs/model.md).
