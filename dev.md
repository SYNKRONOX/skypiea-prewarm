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
| Gateway (`skypiea-gateway`) | container 8080, Service 80 | `POST /mcp`, `DELETE /mcp`, `GET /healthz`, `GET /v1/sessions`, `GET /v1/sessions/{sid}` (used by the CLI), `POST /v1/hint` (only with `SKYPIEA_TURN_HINT=1`) | `skypiea_gateway_calls_total{tool,cold}`, `_added_latency_seconds{tool}`, `_hold_ms{tool}`, `_prewarm_hits_total` / `_misses_total`, `_predictor_latency_seconds`, `_predictor_errors_total`, `_sessions`, ... |
| Controller (`skypiea-controller`) | 8081 (`api`) | `POST /v1/warm`, `POST /v1/inflight`, `GET /v1/state` (used by the CLI and the console) | `skypiea_warm_pool_size{tool}`, `skypiea_pod_seconds_idle_total`, `skypiea_scale_events_total`, `skypiea_scale_refused_total`, `skypiea_scale_errors_total`, queue metrics |
| Predictor service | 8090 | `POST /decide`, `GET /healthz`, `POST /reload`, `POST /rollback` (admin token) | `skypiea_predictor_requests_total`, `_latency_seconds`, `_degraded_total`, `_reloads_total`, `_model_info` |
| Predictor placeholder (stand-in policies) | 8080 | `POST /decide`, `POST /admin/policy` | - |
| Prometheus / Grafana | 9090 / 3000 | chart `monitoring` | - |

Laptop loopback port-forwards: `make demo` gateway :18080 (controller :18081, stub :18082,
model :18083) and Grafana :3000; `make live` console :8090, Grafana :3001, baseline
:18180-18182, predictive :18280-18283 ([`docs/DEMO.md`](docs/DEMO.md)); `make aws-gateway`
:8080 over the SSM tunnel.

The kind scripts take their cluster, registry and ports from the environment (defaults in
brackets): `CLUSTER` [skypiea], `KIND_CTX` [kind-$CLUSTER], `REG_PORT` [5001], `REG_NAME`
[kind-registry], `KIND_REG` [localhost:$REG_PORT], `PF_BASE` [18080; controller +1, stub +2,
model +3], `GRAFANA_PORT` [3000]. Tests and CI use this to run a second cluster beside the
default one ([`docs/TESTING.md`](docs/TESTING.md)). The `make live` ports are fixed because
`console/stacks.py` hard-codes them.

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
| [`agent/`](agent/) | Tool-calling agent: LLM clients (`llm.py`), MCP client, loop, tasks, run harness, summarizer; the SYNTHETIC Skypiea Air world and its tools (`skyair/`), the mock repository (`devrepo/`), Skye's correctness suite (`suite/`) |
| [`console/`](console/) | Live Console: mirrored conversation engine, guardrails (`guards.py`), truth check (`truth.py`), model toggle (`models.py`), pod poller, streaming LLM, model bake-off, web UI (`static/`) |
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
| [`mk/`](mk/) | Make fragments included by the root `Makefile`: `infra.mk`, `model.mk`, `demo.mk`, `console.mk` |
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
- **Skypiea Air world** (`agent/skyair/world.py`, `tools.py`): SYNTHETIC and India-centred,
  generated from a fixed seed on every start: 46 real airport codes, INR fares by a documented
  rule, 306 fictional customers, 930 bookings, a fixed demo-world date (Monday 11 January
  2027). `python -m agent.skyair.world` prints a summary. Docs:
  [`docs/SKYPIEA_WORLD.md`](docs/SKYPIEA_WORLD.md). Tests: `tests/test_skyair.py`,
  `test_skyair_w1.py`, `test_skyair_night.py`.
- **Correctness suite** (`agent/suite/`): 6 scenarios x 3 messy variants with an end-state
  oracle, run through the console's engine with a real local LLM and no cluster:
  `python -m agent.suite.run --models ... --reps N --out ...`, compared by
  `python -m agent.suite.compare`. Results: `results/suite/`,
  [`docs/SUITE_BEFORE_AFTER.md`](docs/SUITE_BEFORE_AFTER.md) (llama3.1:8b 54/54 after; the
  guardrails were tuned on this suite, reps at temperature 0 with a fixed seed are nearly
  identical, and before and after used different worlds).

### Live Console (`console/`)

- `python -m console` (`make live-console`) serves http://127.0.0.1:8090. Runs each agent
  call against two stacks (`skypiea-base`, `skypiea-pred`) through port-forwarded gateways and
  polls both controllers. API: `/api/state`, `/events` (SSE), `/api/chat`, `/api/autopilot`,
  `/api/export.csv`, `/api/<action>`. Docs: [`docs/LIVE_CONSOLE.md`](docs/LIVE_CONSOLE.md),
  [`docs/LIVE_DEMO.md`](docs/LIVE_DEMO.md).
- Binds to 127.0.0.1 only and has **no authentication**: every `POST /api/...` changes state
  (hint, policy, model, predictor kill/restart, reset). Keep it on loopback ([`SECURITY.md`](SECURITY.md)).
- Model toggle (`console/models.py`): local Ollama models with tool calling (`/api/tags`,
  `/api/show`); qwen3 models run with thinking disabled. Every request uses temperature 0 and
  a fixed seed (`console/llm_stream.py`). Default `llama3.1:8b`; install the optional ones with
  `ollama pull qwen2.5:7b` and `ollama pull qwen3:8b` (one model per call).
- Scenarios: three Skypiea Air scenarios (New customer books a trip, Change a booking, Cancel
  and refund) and one code fix; each resets and sends prepared customer lines to the live
  model (Auto-play). In the code and API they are still "stories" (`agent/tasks/stories.py`,
  `POST /api/story`).
- Tests: `tests/test_console.py`, `test_console_models.py`, `test_console_story.py`,
  `test_truth.py`, `test_live_chart.py`.

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

Every check, what it proves and how CI runs it: [`docs/TESTING.md`](docs/TESTING.md).

```bash
make doctor       # machine checks with fixes (tools/doctor.sh; DOCTOR_MODE=test|demo|live)
make setup        # .venv from requirements.lock
make test         # pytest -q (tests/), offline, uses data/samples/
make lint         # ruff check .
make check-sizes  # no tracked file over 50 MB
make check-claims # docs vs committed evidence: numbers, links, make targets, paths, commands (< 1 s)
make secret-sweep # credentials / AWS account ids in the tree (HISTORY=1: all commits)
make infra-check  # lint, tests, sizes, no secret-like file names tracked
make redteam      # predictor red-team suite (tests/redteam)
make audit        # no session data on disk, in logs or metrics
make kind-integration   # functional replay through a running kind cluster
demo/ci_e2e.sh    # CI's end-to-end job: doctor, make demo, scripted agent, tools back to 0
```

`make install-hooks` installs the pre-commit hook (`tools/pre-commit.sh`). CI
([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) has two jobs: `test` (sizes, ruff,
pytest, doctor) and `e2e` (kind on a clean runner, `demo/ci_e2e.sh`).

**AWS targets are guarded.** Every `make aws-*` target (and `push-images-aws*`, `deploy-aws`,
`e0-aws*`) first runs `infra/aws/scripts/aws-guard.sh`: no aws/terraform call without the CLIs,
configured credentials and (except `aws-plan`/`aws-apply`) local Terraform state; targets that
create or start resources also need `AWS_CONFIRM=yes`. `tests/test_aws_guard.py` checks this
with fake binaries, and checks that every AWS target in `mk/infra.mk` has the guard. A new AWS
target must start with `@$(AWS_GUARD) $@` or `@$(AWS_GUARD_CREATE) $@`.

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
| `help` (default), `doctor`, `secret-sweep` | List targets; machine checks; secret sweep |
| `review`, `report`, `check-claims` | Tier 0 + Tier 1 on an own cluster with a PASS/FAIL table (`tools/review.sh`); environment report for issues (`tools/report.sh`); docs vs evidence (`tools/check_claims.py`) |
| `setup`, `lock` | Create `.venv` from `requirements.lock`; re-resolve the lock |
| `test`, `lint`, `check-sizes`, `infra-check`, `install-hooks` | Tests, ruff, size check, all checks, git hook |
| `data`, `data-all`, `samples` | Trace pipeline for one / all sources; rebuild the committed samples |
| `build-images`, `clean-images`, `measure-startup` | Tool + platform images (tag: `tools/image_tag.sh`, the last commit that changed an image input); remove them; image size and startup per tool |
| `cluster` / `cluster-kind`, `deploy` / `deploy-kind`, `deploy-kind-e0`, `clean-cluster` | kind cluster, Helm install, E0 layout, teardown |
| `e1`, `e0-kind`, `e0-kind-parallel` | Accuracy experiment; E0 cold starts on kind |
| `kind-integration`, `tool-map-coverage`, `grafana-kind` | Functional replay; traffic share per server; Grafana :3000 |
| `model-all`, `predictor-image`, `results-summary` | Tune, simulate, export bundles; predictor image; rebuild `docs/results_summary.md` |
| `redteam`, `audit`, `loadtest` | Predictor red team, no-persistence audit, load test |
| `demo`, `demo-portforward`, `demo-failures`, `demo-clean` | Whole stack on kind ([`docs/DEMO.md`](docs/DEMO.md)) |
| `agent-demo`, `agent-live` | Agent through the gateway ([`docs/AGENT.md`](docs/AGENT.md)) |
| `live`, `live-pf`, `live-console`, `live-screenshot`, `live-clean` | Live Console ([`docs/LIVE_CONSOLE.md`](docs/LIVE_CONSOLE.md), [`docs/LIVE_DEMO.md`](docs/LIVE_DEMO.md)) |
| `watch-pods`, `fr6-reload`, `hold-report` | Pod events of both console stacks; FR-6 reload check; FR-9 hold report (`mk/console.mk`) |
| `fetch-models`, `models-archive` | Download the bundles from release v1.2 of `MODELS_REPO` (default `SYNKRONOX/skypiea-prewarm`; `MODELS_REPO=""` uses origin if it is GitHub; `FETCH_DRY_RUN=1` prints the URL); build the archive to publish ([`docs/PORTABLE_MODELS.md`](docs/PORTABLE_MODELS.md)) |
| `aws-plan`, `aws-apply`, `aws-tunnel`, `aws-kubeconfig`, `aws-shell`, `push-images-aws`, `push-images-aws-docker`, `deploy-aws`, `e0-aws`, `e0-aws-parallel`, `e0-aws-node`, `e0-aws-status`, `e0-aws-fetch`, `aws-gateway`, `aws-stop`, `aws-start`, `aws-destroy`, `aws-reminder` | AWS lifecycle ([`infra/aws/README.md`](infra/aws/README.md)) |

`make help` (or plain `make`) lists every target with its one-line description.

## 7. Training data

What each predictor profile was trained and tuned on (sessions / calls per split, copied by
`tools/training_data_table.py` from the pipeline's split reports; check with `--check`):

<!-- training-data:start (generated by tools/training_data_table.py; do not edit) -->
| Profile | Dataset | Role | TRAIN | VAL | TEST | Note |
|---|---|---|---|---|---|---|
| support | tau2_bench | ranker trained on TRAIN; policy tuned on VAL; evaluated on TEST | 99 / 503 | 28 / 171 | 56 / 342 | airline tasks; the users in tau2-bench are LLM-simulated, and tool times come from a simulated run (data/README.md) |
| support | tau_bench | listed in the profile's sources, but without timestamps it cannot be replayed, so the VAL pick used tau2 only (configs/model/profiles.yaml); next-tool accuracy only | 1,073 / 7,785 | 272 / 2,141 | 583 / 4,327 | has no timestamps |
| coding | tracelab | ranker trained on TRAIN; policy tuned on VAL; evaluated on TEST | 4,532 / 435,501 | 358 / 73,230 | 2,092 / 225,290 | real coding-agent traces with per-call timings (data/README.md) |
| coding | copilot_2026 | policy tuned on VAL (with TraceLab); evaluated on TEST | 663 / 29,114 | 158 / 8,299 | 398 / 18,322 | one session per group |
| prd (SYNTHETIC) | synthetic_prd | demo bundle for `make demo`; not used by the Live Console | 2,520 / 7,359 | 630 / 1,820 | 1,350 / 3,892 | generated from the PRD scenario graphs (python -m traces.generate_synthetic) |

Sessions / calls per split; source and input hashes: [`docs/training_data.md`](docs/training_data.md).
<!-- training-data:end -->

## 8. Where results come from

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
