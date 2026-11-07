# Reviewer guide

Two pages: what Skypiea is, how to check it in the time you have, how to use it yourself, and
where each PRD requirement is shown. Every command below was run on 2026-11-07 on the tested
laptop (Windows 11 + WSL2, Ubuntu 24.04, x86-64); timings are from that run
([`handoff/NIGHT_W2.md`](handoff/NIGHT_W2.md)).

## 1. The 30-second view

AI agents call tools in patterns. When each tool is a scale-to-zero container, the first call
to a cold tool waits seconds for a pod. Skypiea learns the patterns from tool-call metadata
only (names, timings; never prompts or arguments) and starts the next tool's pod before the
call arrives, then lets it go back to zero.

Evidence: CI runs on every push to `main`
([latest runs](https://github.com/SYNKRONOX/skypiea-prewarm/actions/workflows/ci.yml));
every result table, generated from the committed files in `results/`:
[`results_summary.md`](results_summary.md); what we met, changed or did not do:
[`PRD_STATUS.md`](PRD_STATUS.md); what on the demo screen is measured, scripted or mocked:
[`MEASURED_VS_SCRIPTED.md`](MEASURED_VS_SCRIPTED.md). `make check-claims` checks that the
README's numbers match those files.

## 2. Pick your path

| Path | Time (our run) | Needs | You get |
|---|---|---|---|
| **Tier 0** | about 5 min | Python 3.12, make, git | tests, lint, size check |
| **Tier 1** | about 10 min more | + Docker (6 GB), kind, kubectl, Helm | the whole stack on kind: replay, scripted agent, failure injection |
| **Tier 2** | about 10 min more | + Ollama with `llama3.1:8b`, network for the bundles | the Live Console (chat, two stacks side by side) |
| Tier 3 | skip | your own AWS account; costs money | the AWS measurements are already committed in `results/` |

**Versions** (tested in brackets; `make doctor` checks them and prints a fix per problem):

```bash
python3.12 --version            # 3.12.x (3.12.2); other versions are refused: requirements.lock is for 3.12
make --version | head -1        # GNU Make (4.3)
docker version --format '{{.Server.Version}}'   # (29.9.2); Docker needs >= 6 GB of memory
kind version; kubectl version --client; helm version --short   # (v0.33.0, v1.37.2, v4.3.1)
```

**One command for Tier 0 + Tier 1:** `make review` (or `make review MODE=test` for Tier 0
only). It prints a PASS/FAIL table with timings and ends with `review: PASS`. Tier 1 runs on
its **own** cluster `skypiea-review` (registry :5002, ports 28080-28083 and 3300) and deletes
only that cluster at the end, so it never touches a `skypiea` cluster you already have. Our
run: 818 s in total (Tier 0 280 s, `make demo` 295 s, `make agent-demo` 214 s,
`make demo-failures` 27 s). Logs per step in `.review/`.

**Tier 0 by hand** (no Docker):

```bash
git clone https://github.com/SYNKRONOX/skypiea-prewarm.git && cd skypiea-prewarm
make doctor DOCTOR_MODE=test    # last line: doctor: all required checks passed (0 WARN).
make setup                      # 59 s on a fresh clone; last line: No broken requirements found.
make test                       # about 4 min; last line: N passed, M skipped (no failures)
make lint check-sizes           # All checks passed!  /  OK: ... tracked files, none over 50 MB.
make check-claims               # check-claims: ...; 0 mismatch(es)
```

No Python 3.12 but Docker? `make test-docker` runs the same checks in a `python:3.12` container
(231 s in our run, image pull included). Clean-up: delete the clone (the venv is in `.venv/`).

**Tier 1 by hand** (Docker running):

```bash
make doctor                     # also Docker, its memory, kind, kubectl, helm, ports
make demo                       # 8 steps; "FAILURES: none" after each replay; a scale_to_zero vs predictive table
make agent-demo                 # scripted LLM through the gateway; last line names .demo/agent_runs/summary.md
make demo-failures              # predictor stopped, controller killed: every call must still succeed
make demo-clean                 # port-forwards off, cluster and registry deleted, .demo/ removed
```

Already have a `skypiea` cluster (for example from `make live`)? `make demo` reuses it. Use
another one: `CLUSTER=skypiea-rt REG_PORT=5002 REG_NAME=kind-registry-rt PF_BASE=28080
GRAFANA_PORT=3300 make demo`, and the same `CLUSTER`/`REG_NAME` for `make demo-clean`.
Images stay after clean-up (`make clean-images`).

**Tier 2** (Live Console): `ollama pull llama3.1:8b` (one model per call; `qwen2.5:7b` and
`qwen3:8b` are optional), `make fetch-models`, `make live`, open http://127.0.0.1:8090, press a
scenario button; `make live-clean` stops it. Details: README Tier 2 and
[`LIVE_DEMO.md`](LIVE_DEMO.md). Without Ollama the console cannot run; use Tier 1.

## 3. Use Skypiea yourself (after `make demo`)

The gateway is a stateless MCP endpoint (MCP revision 2026-07-28): `tools/list` and
`tools/call` work directly; **`initialize` is not supported** (answers -32601), so MCP clients
that insist on the older handshake cannot connect. Commands for the default ports
(`PF_BASE=28080` → use 28080 / 28083).

```bash
GW=http://127.0.0.1:18080/mcp
curl -s $GW -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
# a cold call, then the call the predictor expects next (wait a few seconds in between)
curl -s -D - $GW -H 'Content-Type: application/json' -H 'Mcp-Session-Id: me-1' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"read_file","arguments":{}}}'
curl -s -D - $GW -H 'Content-Type: application/json' -H 'Mcp-Session-Id: me-1' \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"run_tests","arguments":{}}}'
```

Expected: the first answer has `x-skypiea-cold: 1` and an `x-skypiea-added-ms` of a few
seconds (ours: 2288.9); the second `x-skypiea-cold: 0`, `x-skypiea-predicted: 1` (ours:
added 0.1 ms). The CLI shows why:

```bash
bin/skypiea --context kind-skypiea status            # tools, replicas, warm pool, policy
bin/skypiea --context kind-skypiea sessions          # live sessions in the gateway
bin/skypiea --context kind-skypiea trace me-1        # per call: COLD/WARM, predicted, held ms, added ms
bin/skypiea --context kind-skypiea explain me-1      # "ranked run_tests 1.00 ... HIT: it was predicted"
kubectl --context kind-skypiea get pods -n skypiea-tools --watch   # pods appear and go back to 0 (Ctrl-C)
curl -s http://127.0.0.1:18083/decide -H 'Content-Type: application/json' \
  -d '{"session_id":"x","now_ms":2300,"history":[{"tool_class":"read_file","t_start_ms":0,"t_end_ms":2300,"error":false,"batch_id":0}]}'
```

The last command asks the predictor directly (metadata only, contract in
[`predictor_contract.md`](predictor_contract.md)); ours answered `run_tests` p 0.998, warm 1
copy, in 0.8 ms. The `make demo` predictor is the SYNTHETIC `prd` profile. Ended sessions
are purged by the gateway ([`gateway_controller.md`](gateway_controller.md)). **You cannot plug in your own tool server at runtime**: a new
server needs a folder in `tools/`, an entry in `tools/tool_map.yaml` and the chart, an image
build and a redeploy (`dev.md`, "How to add things"; not tried with a third-party server).

## 4. PRD requirements: where to see them

| PRD item | Status | Show it | Expect |
|---|---|---|---|
| FR-1 top-3 in <= 50 ms | Met (service alone) | `pytest tests/test_service.py::test_decide_answers_follow_the_contract`; `results/loadtest.csv` | pass; p99 13.2 / 15.9 ms at 75 / 150 req/s |
| FR-2 pod Ready before the call >= 80% | Partly met | `results_summary.md` "Baselines and FR-2" | 86.8% / 90.9%, at tau 0.4, in the simulator |
| FR-3 evict at TTL + 5 s | Met | `pytest tests/test_controller.py::test_warm_then_scale_to_zero_at_ttl_plus_purge`; the pod watch above | a pod is removed at its TTL + 5 s after last use (default TTL 30 s; a decision can set its own, 82 s in our run) |
| FR-4 fall back to scale-from-zero | Met | `make demo-failures`; `pytest tests/test_service.py::test_decide_fails_open` | every call succeeds with the predictor down |
| FR-5 Prometheus / Grafana | Met | Grafana http://127.0.0.1:3000 after `make demo`; `pytest tests/test_monitoring.py` | cold/warm counters, warm-pool size |
| FR-6 reload < 2 s, no dropped answers | Met (prepared bundle, laptop) | `pytest tests/test_service.py::test_reload_rollback_and_refusals`; `results/fr6_reload_check.json` | 0.49-0.50 s per reload, 0 non-200 |
| FR-7 sessions independent | Met | `pytest tests/test_gateway.py::test_sessions_isolated_created_and_purged` | pass |
| FR-8 replay at 1x/5x/10x within 5% | Partly met | `pytest tests/test_replayer.py` | 1x for results; 5x/10x not run |
| FR-9 150 ms hold | Partly met | `pytest tests/test_gateway_hold.py`; `x-skypiea-held-ms` on a cold call (ours 150.0) | hold <= 150 ms; race rate not measured |
| NFR-1 / NFR-2 latency | Changed / Partly met | `results/loadtest.csv`, `results/scenario_aws_summary.csv` | service alone; 20-session AWS runs |
| NFR-3 idle <= 30% of always-warm | Met (simulator) | `results/scenario_aws_summary.csv` (idle pod-s column) | predictive 9.7% / 19.1% of always-warm on AWS |
| NFR-4 500-session soak | **Not done** | `results/concurrent40_aws.csv` | 40 sessions only: 938 of 938 calls |
| NFR-5 reconcile at 50 nodes | **Not done** | none | one-node cluster |
| NFR-6 no session data kept | Met (ended sessions) | `make audit` (needs a bundle in `models/`) | audit passes |
| OpenFaaS, LangGraph, Locust | Changed | `PRD_STATUS.md` "Scope" | own operator, own agent loop, own load tester |

## 5. Top 10 problems (from our fresh-clone runs and `make doctor`)

| You see | Do |
|---|---|
| `Python 3.12 not found` | Install 3.12 (`make doctor` prints the command), or `make setup PYTHON=/path/to/python3.12` |
| `python3.10 is on PATH but does not run (a pyenv shim ...)` | Point to a real binary: `PYTHON=$(pyenv root)/versions/3.12.x/bin/python3.12` |
| `is not a working Python 3.12` / `.venv exists but is not Python 3.12` | Delete the `.venv` folder, then `make setup` with 3.12 |
| `Docker is installed but the daemon is not reachable` | Start Docker Desktop (WSL2: WSL integration on) or `sudo systemctl start docker` |
| `Docker memory ... (need >= 6 GB)`, pods `Pending`/`OOMKilled` | WSL2: `memory=8GB` under `[wsl2]` in `%UserProfile%\.wslconfig`, then `wsl --shutdown` |
| `demo ports in use` | `make demo-clean` if it is an earlier demo, else `PF_BASE=28080 GRAFANA_PORT=3300 make demo` |
| `:5001 is used by another program` | `REG_PORT=5002 REG_NAME=kind-registry-2 make demo` |
| `make review`: cluster `skypiea-review` already exists | Left alone on purpose. `CLUSTER=skypiea-review REG_NAME=kind-registry-review make demo-clean` if it is yours |
| `Unable to connect to the server: EOF` after sleep | `docker restart skypiea-control-plane skypiea-worker`, then `make demo-portforward` |
| `ollama pull a b`: "accepts 1 arg(s)" / console says the LLM is down | One model per `ollama pull`; start `ollama serve` |

Anything else: `make report` prints what to paste into an issue (no secrets; your home
directory shown as `~`). Known gaps: [`KNOWN_ISSUES.md`](KNOWN_ISSUES.md).

## 6. Honest limits

- **Tested:** Windows 11 + WSL2 (Ubuntu 24.04) on x86-64 (our laptop), and Linux x86-64 in CI
  (GitHub `ubuntu-latest`, test and e2e jobs). **Untested:** macOS (Intel or Apple silicon),
  Linux arm64, other distributions. The scripts use GNU `sha256sum`, `ss`, `setsid`.
- **Python 3.12 only** (`requirements.lock`); 3.10 is refused by `make doctor` and `make setup`.
- **Docker needs at least 6 GB of memory** and about 15 GB of free disk for Tier 1.
- **Not reproducible by you:** the AWS timings (one m7i-flex.large we no longer run; the
  measured files are committed), and exact LLM conversations (temperature 0 and a fixed seed
  make them repeatable on one machine, but model and Ollama versions change the text). kind
  timings depend on your laptop and are a lower bound.
- Everything on kind is a **demo run, not a result**; the Skypiea Air world is **synthetic**;
  the Live Console has **no authentication** and must stay on 127.0.0.1 (`../SECURITY.md`).
- Full list: README "Known limitations" and [`limits.md`](limits.md).
