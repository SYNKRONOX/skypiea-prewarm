# Skypiea developer guide

How the code fits together, where each piece lives, and how to change it. This page links to
the detailed docs and does not repeat them. User-facing overview and results:
[`README.md`](README.md). Requirements: [`PRD.md`](PRD.md), status per requirement:
[`docs/PRD_STATUS.md`](docs/PRD_STATUS.md). Index of every doc: [`docs/README.md`](docs/README.md).

## 1. Architecture and the request path

```
agent / replayer / console --POST /mcp--> gateway --forward--> ready tool pod (0..N per server)
                                           |  ^                        ^
                         (after the reply) |  | EndpointSlices         | replicas 0 <-> N
                                           v  |                        |
                          predictor POST /decide --> warm decisions    |
                                           |                           |
                                           +--POST /v1/warm, /v1/inflight--> controller (kopf)
```

1. A client sends a JSON-RPC `tools/call` to the gateway's `POST /mcp` (session in the
   `Mcp-Session-Id` header).
2. The gateway maps the tool name to a **tool class** and a **server** with
   [`tools/tool_map.yaml`](tools/tool_map.yaml), and forwards the call to a ready pod address
   from the server's EndpointSlice.
3. No ready pod: the gateway asks the controller for one (`reason=demand`), holds the call up to
   150 ms (FR-9), then takes the cold path (waits for Ready, up to 120 s) and marks the call
   cold.
4. After the reply, off the critical path, the gateway posts the session's metadata (tool
   classes, timings, batch structure; never arguments) to the predictor's `POST /decide`
   (300 ms timeout). Each `warm` decision is forwarded to the controller after its
   `start_in_ms`.
5. The controller scales Deployments up (at most 2 starts at once per node, queued by due time)
   and back to 0 at TTL + 5 s after last use. It never scales below the in-flight count.
6. If the predictor is dead, slow or off-contract, the call is unaffected: plain scale-to-zero
   (FR-4).

Full description, failure behaviour and live checks:
[`docs/gateway_controller.md`](docs/gateway_controller.md). Predictor API:
[`docs/predictor_contract.md`](docs/predictor_contract.md).

### Ports, endpoints and metrics (in the cluster; all Services are ClusterIP)

| Component | Port | Endpoints | Metrics (`GET /metrics`) |
|---|---|---|---|
| Gateway (`skypiea-gateway`) | container 8080, Service 80 | `POST /mcp`, `DELETE /mcp`, `GET /healthz`, `POST /v1/hint` (only with `SKYPIEA_TURN_HINT=1`) | `skypiea_gateway_calls_total{tool,cold}`, `_added_latency_seconds{tool}`, `_hold_ms{tool}`, `_prewarm_hits_total` / `_misses_total`, `_predictor_latency_seconds`, `_predictor_errors_total`, `_sessions`, ... |
| Controller (`skypiea-controller`) | 8081 (`api`) | `POST /v1/warm`, `POST /v1/inflight`, `GET /v1/state` (used by the CLI and the console) | `skypiea_warm_pool_size{tool}`, `skypiea_pod_seconds_idle_total`, `skypiea_scale_events_total`, `skypiea_scale_refused_total`, `skypiea_scale_errors_total`, queue metrics |
| Predictor service | 8090 | `POST /decide`, `GET /healthz`, `POST /reload`, `POST /rollback` (admin token) | `skypiea_predictor_requests_total`, `_latency_seconds`, `_degraded_total`, `_reloads_total`, `_model_info` |
| Predictor placeholder (stand-in policies) | 8080 | `POST /decide`, `POST /admin/policy` | - |
| Prometheus / Grafana | 9090 / 3000 | chart `monitoring` | - |

Laptop loopback port-forwards: `make demo` gateway :18080 and Grafana :3000; `make live`
console :8090 and Grafana :3001 ([`docs/DEMO.md`](docs/DEMO.md)); `make aws-gateway` :8080
over the SSM tunnel.

## 2. Top-level folders

| Folder | Contents |
|---|---|
| [`gateway/`](gateway/) | MCP gateway (`app.py`, `main.py`), routing, readiness tracking, predictor/controller clients, the placeholder predictor with stand-in policies (`predictor_stub.py`), scenario runner and checks (`scenario_run.py`, `replay_check.py`, `concurrent_check.py`) |
| [`controller/`](controller/) | kopf scale-to-zero operator: pure warm-pool logic (`core.py`), runtime and HTTP API (`service.py`), kopf entry (`operator.py`) |
| [`services/predictor/`](services/predictor/) | Predictor service (Starlette): `app.py`, `engine.py`, bundle `export.py`, `schema.py`, `loadtest.py`, Dockerfile |
| [`predictors/`](predictors/) | Models and policies: Markov/n-gram graph (`graph.py`, `markov.py`), LightGBM ranker (`lgbm_ranker.py`), baselines, warm decisions (`decide.py`, `ladder.py`), evaluation (`eval.py`, `eval_paper.py`) |
| [`sim/`](sim/) | Discrete-event warm-pool simulator, tuning (`tune.py`, `retune.py`), lookahead study, figures, `results_summary.py` |
| [`traces/`](traces/) | Trace schema, one converter per dataset (`convert_*.py`), `pipeline.py`, `generate_synthetic.py`, tool classes |
| [`data/`](data/) | `download.py`, `MANIFEST.json`, committed `samples/`; `raw/` and `processed/` are not committed |
| [`replayer/`](replayer/) | Open-loop trace replayer (`replay.py`, configs) |
| [`agent/`](agent/) | Tool-calling agent: LLM clients (`llm.py`), MCP client, loop, tasks, run harness, summarizer |
| [`console/`](console/) | Live Console: mirrored conversation engine, pod poller, streaming LLM, web UI (`static/`) |
| [`demo/`](demo/) | Scripts behind `make demo` / `make live` / `make fetch-models`: deploy, port-forwards, failure injection, console driver, table generators |
| [`tools/`](tools/) | 16 MCP tool servers (one folder each), shared server code (`common/`), `tool_map.yaml`, image build and repo checks |
| [`charts/skypiea/`](charts/skypiea/) | Helm chart: tools at 0 replicas, platform (gateway, controller, predictor), monitoring |
| [`deploy/`](deploy/) | kind cluster config and the platform image Dockerfile |
| [`infra/aws/`](infra/aws/) | Terraform for one EC2 k3s node (SSM only), ECR, S3; scripts |
| [`experiments/`](experiments/) | E0 cold-start harness and probe, experiment configs |
| [`configs/`](configs/) | Tool classes, vocabularies, model/policy/simulator/pricing configs, measured cold starts, live-console config |
| [`skypiea_cli/`](skypiea_cli/), [`bin/`](bin/) | `skypiea` CLI |
| [`tools_audit/`](tools_audit/) | No-persistence audit (`make audit`) |
| [`tests/`](tests/) | pytest suite (offline), `tests/redteam/` |
| [`results/`](results/) | Committed result files |
| [`docs/`](docs/) | Design, results, limits, demo guides, handoff reports |
| [`mk/`](mk/) | Make fragments included by the root `Makefile`: `infra.mk`, `model.mk`, `demo.mk` |
| [`models/`](models/) | Predictor bundles (not committed; only the README) |
| [`shaper/`](shaper/), [`report/`](report/) | Early placeholders: the shaper became the gateway's hold; `report/` is empty |

## 3. Components

### Gateway (`gateway/`)

- Entry: `python -m gateway.main gateway` (port 8080). Config from `SKYPIEA_*` environment
  variables set by the chart's `platform` values (`gateway/main.py`).
- Talks to: tool pods (MCP over HTTP), the predictor (`POST /decide`, contract in
  [`docs/predictor_contract.md`](docs/predictor_contract.md)), the controller
  (`POST /v1/warm`, `/v1/inflight`), the Kubernetes API (EndpointSlice watch).
- Stand-in policies for baselines (`scale_to_zero`, `always_warm`, `keep_alive`, `markov1`,
  `predictive`, `oracle`): `python -m gateway.main predictor`, docstring of
  `gateway/predictor_stub.py`. Every baseline runs through the same gateway path.
- Tests: `tests/test_gateway.py`, `test_gateway_mcp.py`, `test_gateway_hint.py`,
  `test_early_connect.py`, `test_scenario_run.py`, `test_concurrent_check.py`.

### Controller (`controller/`)

- Entry: `kopf run --standalone --namespace skypiea-tools -m controller.operator`.
- Pure logic in `core.py` (TTL, caps, per-node scale-up limit, queue by due time, rebuild from
  Deployment annotations after a restart); `service.py` wraps it with the HTTP API and a
  reconcile tick (0.25 s).
- Config: chart values (`defaultTtlS`, `purgeS`, `maxScaleUpsPerNode`, caps in
  `configs/model/decide.yaml`).
- Tests: `tests/test_controller.py`. Docs: [`docs/gateway_controller.md`](docs/gateway_controller.md).

### Predictor service (`services/predictor/`) and models (`predictors/`)

- Entry: `python -m services.predictor` (PORT 8090, WORKERS 1). Loads a bundle from
  `MODEL_PATH`; `/reload` and `/rollback` need `ADMIN_TOKEN` and accept bundles only under
  `MODEL_ROOT`.
- One profile per deployment: `support`, `coding`, or SYNTHETIC `prd`
  (`configs/model/profiles.yaml`). Graph, ranker and decision configs: `configs/model/`.
- Bundles: `python -m services.predictor.export` ([`models/README.md`](models/README.md)), or
  `make model-all`; image: `make predictor-image`.
- Tests: `tests/test_service.py`, `test_service_hint.py`, `test_service_overrides.py`,
  `test_decide.py`, `test_graph.py`, `test_lgbm_ranker.py`, `test_predictors.py`,
  `test_no_leakage.py`, `test_eval.py`, `tests/redteam/`. Docs:
  [`docs/model.md`](docs/model.md), [`docs/redteam.md`](docs/redteam.md).

### Simulator (`sim/`)

- Runs the same decision code (`predictors.decide.Decider`) over TEST sessions with timing:
  `python -m sim.offline --cold-start PATH`, `python -m sim.figures`,
  `python -m sim.results_summary` (`make results-summary`). See [`sim/README.md`](sim/README.md).
- Config: `configs/model/sim.yaml`, cold starts from `configs/cold_start.yaml`
  ([`docs/cold_start_schema.md`](docs/cold_start_schema.md)).

### Traces and data (`traces/`, `data/`)

- Schema: [`docs/schema.md`](docs/schema.md), `traces/schema.py`.
- `python -m traces.pipeline --source <name>` converts `data/raw/<source>` to
  `data/processed/<source>/` (`make data`, `make data-all`); `python -m traces.generate_synthetic`
  writes the SYNTHETIC PRD traces. Dataset notes and licences: [`data/README.md`](data/README.md).

### Replayer (`replayer/`)

- Open-loop replay with per-call CSV; `results` mode requires real gaps (`gap_scale = 1`).
  Cluster scenario runs use `gateway/scenario_run.py`. Tests: `tests/test_replayer.py`.

### Agent (`agent/`)

- `python -m agent.run` via `make agent-demo` / `make agent-live`; LLM is scripted or any
  OpenAI-compatible endpoint. Talks only to the gateway's `POST /mcp`. Records metadata only.
  Docs: [`docs/AGENT.md`](docs/AGENT.md). Tests: `tests/test_agent.py`, `test_vocab.py`,
  `test_live_tasks.py`.

### Live Console (`console/`)

- `python -m console` (`make live-console`) serves http://127.0.0.1:8090. Runs each agent
  call against two stacks (`skypiea-base`, `skypiea-pred`) through port-forwarded gateways and
  polls both controllers. API: `/api/state`, `/events` (SSE), `/api/chat`, `/api/autopilot`,
  `/api/export.csv`, `/api/<action>`. Docs: [`docs/LIVE_CONSOLE.md`](docs/LIVE_CONSOLE.md).
  Tests: `tests/test_console.py`, `test_live_chart.py`.

### Tools (`tools/`)

- One folder per server (`Dockerfile`, `server.py`), shared code in `tools/common/` (MCP
  server, tiers light/medium/heavy, service-time model). `tools/build_images.sh`
  (`make build-images`). Each server exposes one MCP tool named like the server.
- Tests: `tests/test_tools_mcp.py`, `test_tool_map_coverage.py`, `test_chart_matches_tool_map.py`.

### Chart (`charts/skypiea/`), kind (`deploy/`), AWS (`infra/aws/`)

- `make cluster deploy` (kind) or `make deploy-aws` (k3s). Values files per target
  (`values-kind.yaml`, ...). Infra docs: [`docs/infra.md`](docs/infra.md),
  [`infra/aws/README.md`](infra/aws/README.md), [`docs/aws_plan.md`](docs/aws_plan.md).
- Tests: `tests/test_monitoring.py`, `test_live_chart.py`, `test_aws_infra.py`.

### Experiments (`experiments/`)

- E0 cold starts: `experiments/e0_cold_start.py` with `e0_config.yaml` (`make e0-kind`,
  `make e0-aws-node`); `e0_write_cold_start.py` writes `configs/cold_start.yaml`. Next-tool
  accuracy: `python -m experiments.run --config experiments/e1_predictor_accuracy.yaml`.
  Docs: [`docs/experiments.md`](docs/experiments.md), [`docs/benchmark_protocol.md`](docs/benchmark_protocol.md).

## 4. Tests and lint

```bash
make setup        # .venv from requirements.lock
make test         # pytest -q (tests/), offline, uses data/samples/
make lint         # ruff check .
make check-sizes  # no tracked file over 50 MB
make infra-check  # lint, tests, sizes, no secrets tracked
make redteam      # predictor red-team suite (tests/redteam)
make audit        # no session data on disk, in logs or metrics
make kind-integration   # functional replay through a running kind cluster
```

`make install-hooks` installs the pre-commit hook (`tools/pre-commit.sh`).

## 5. How to add things

**A tool server.** Add `tools/<name>/` (`Dockerfile`, `server.py`, following an existing server
of the same tier); add it under `servers:` in `tools/tool_map.yaml` with its tier, work type
and the tool classes it serves; add it to the chart's `tools:` list in
`charts/skypiea/values.yaml` (`tests/test_chart_matches_tool_map.py` checks they match);
`make build-images`, then measure its cold start with E0 so `configs/cold_start.yaml` has it.
Check coverage with `python tools/tool_map_coverage.py data/processed`.

**A baseline policy.** For cluster runs, add it to `gateway/predictor_stub.py` (it then runs
through the same gateway path; select with `SKYPIEA_POLICY` or `POST /admin/policy`). For the
simulator, implement the policy in `predictors/` (`predictors.base` interface, see
`baselines.py`) and add a `Spec` for it to the simulator's policy list in `sim/offline.py`. A new shipped
profile goes in `configs/model/profiles.yaml` and is tuned on VAL only
([`docs/model.md`](docs/model.md)).

**A dataset.** Add `traces/convert_<name>.py` producing schema sessions (metadata only, no
prompts or arguments), register it in `SOURCES` in `traces/pipeline.py`, map its tools to
classes in `configs/tool_classes/<name>.yaml`, document download and licence in
`data/README.md` (and `data/download.py`), add a small sample to `data/samples/`
(`tools/make_sample.py`) and tests (`tests/test_pipeline.py`, `test_samples.py`). Every class
must reach a server (`tests/test_tool_map_coverage.py`).

## 6. Make targets

| Target | What it does |
|---|---|
| `setup`, `lock` | Create `.venv` from `requirements.lock`; re-resolve the lock |
| `test`, `lint`, `check-sizes`, `infra-check`, `install-hooks` | Tests, ruff, size check, all checks, git hook |
| `data`, `data-all`, `samples` | Trace pipeline for one / all sources; rebuild the committed samples |
| `build-images`, `measure-startup` | Tool + platform images; image size and startup per tool |
| `cluster` / `cluster-kind`, `deploy` / `deploy-kind`, `deploy-kind-e0`, `clean-cluster` | kind cluster, Helm install, E0 layout, teardown |
| `e1`, `e0-kind`, `e0-kind-parallel` | Accuracy experiment; E0 cold starts on kind |
| `kind-integration`, `tool-map-coverage`, `grafana-kind` | Functional replay; traffic share per server; Grafana :3000 |
| `model-all`, `predictor-image`, `results-summary` | Tune, simulate, export bundles; predictor image; rebuild `docs/results_summary.md` |
| `redteam`, `audit`, `loadtest` | Predictor red team, no-persistence audit, load test |
| `demo`, `demo-portforward`, `demo-failures`, `demo-clean` | Whole stack on kind ([`docs/DEMO.md`](docs/DEMO.md)) |
| `agent-demo`, `agent-live` | Agent through the gateway ([`docs/AGENT.md`](docs/AGENT.md)) |
| `live`, `live-pf`, `live-console`, `live-screenshot`, `live-clean`, `fetch-models` | Live Console ([`docs/LIVE_CONSOLE.md`](docs/LIVE_CONSOLE.md)) |
| `aws-plan`, `aws-apply`, `aws-tunnel`, `aws-kubeconfig`, `aws-shell`, `push-images-aws`, `deploy-aws`, `e0-aws-node`, `e0-aws-status`, `e0-aws-fetch`, `aws-gateway`, `aws-stop`, `aws-start`, `aws-destroy`, `aws-reminder` | AWS lifecycle ([`infra/aws/README.md`](infra/aws/README.md)) |

`grep -hE '^[a-z0-9_.-]+:.*##' Makefile mk/*.mk` lists every target with its one-line help.

## 7. Where results come from

| Result | Produced by | Files |
|---|---|---|
| E0 cold starts | `experiments/e0_cold_start.py` (`make e0-kind`, `make e0-aws-node` + `e0-aws-fetch`) | `results/e0_*` |
| AWS scenario runs | `gateway/scenario_run.py` in the cluster (`docs/handoff/MORNING_REPORT.md`, `OVERNIGHT_REPORT.md`) | `results/scenario_aws*` |
| Simulator, tuning, oracle, lookahead | `sim/offline.py`, `sim/tune.py`, `sim/retune.py`, `sim/lookahead.py`, `sim/figures.py` | `results/sim_*`, `results/tau_curve*.png` |
| Next-tool accuracy | `predictors/evaluate.py`, `predictors/phase4.py`, `predictors/eval_paper.py` | `results/p1_accuracy.csv.gz`, `results/p4_*`, `results/paper_protocol.csv` |
| Load test | `services/predictor/loadtest.py` (`make loadtest`) | `results/loadtest.csv` |
| Live agent (demo runs) | `make agent-live`, `python -m agent.summarize` | `results/agent_runs/` |
| Live Console (demo runs) | `demo/console_run.py`, `python -m demo.live_runs_table`, `python -m demo.leadtime_table` | `results/live_console_demo/`, `results/live_console_leadtime/` |
| Summary tables | `sim/results_summary.py` (`make results-summary`) | `docs/results_summary.md` |

Rules: results come from real gaps (`results` mode); kind timings are functional only; every
quoted number has a source in [`docs/numbers_and_sources.md`](docs/numbers_and_sources.md);
limits in [`docs/limits.md`](docs/limits.md).
