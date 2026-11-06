# Testing: every check, what it covers, how to reproduce it

All commands run from the repository root. Tiers refer to the README's
[Install and use](../README.md#install-and-use).

| Check | Command | Needs | What it proves | Where it runs |
|---|---|---|---|---|
| Environment | `make doctor [DOCTOR_MODE=test\|demo\|live]` | bash | The machine has what the tier needs; prints the fix for each FAIL | you; CI (`test` job: `test` mode, `e2e` job: `demo` mode) |
| Unit and integration tests | `make test` | Tier 0 | See "What the tests cover" below | you; CI `test` job |
| Lint | `make lint` | Tier 0 | `ruff check .` with the rules in `pyproject.toml` | you; CI `test` job |
| Size check | `make check-sizes` | Python | No tracked file is over 50 MB (`tools/check_sizes.py`); `make install-hooks` adds the same check as a pre-commit hook | you; CI `test` job |
| Secret sweep | `make secret-sweep` (`HISTORY=1` for every commit) | git | No credential-like text, no unmasked AWS account id (ECR hosts and ARNs, data files included), no secret-like file names; prints file names only | you |
| Training-data table | `python tools/training_data_table.py --check` | `data/processed/` (datasets) | The table in README, dev.md and `docs/training_data.md` matches the pipeline's split reports | you, with the datasets |
| Demo | `make demo` | Tier 1 | The whole stack deploys on kind; every replayed call succeeds under scale-to-zero and the predictor (exit 1 otherwise) | you; CI `e2e` job |
| Scripted agent | `make agent-demo` | `make demo` up | An agent with a scripted LLM completes Scenario A and B through `POST /mcp` only; exit 1 on a failed call | you; CI `e2e` job |
| Failure injection | `make demo-failures` | `make demo` up | With the predictor stopped and the controller killed, every probe call still succeeds | you |
| End to end | `demo/ci_e2e.sh` | Tier 1 | doctor, `make demo`, `make agent-demo`, then all 16 tool Deployments back to 0 replicas within 300 s | you; CI `e2e` job |
| Predictor red team | `make redteam` | Tier 0 | `tests/redteam/`: malformed and oversized requests, hostile tool names and session ids, concurrency, bad bundles and reloads against the predictor service; under 10 minutes | you |
| Privacy audit | `make audit` | a bundle in `models/` | No session data on disk, in logs or in metrics; kill during reload | you |
| AWS refusal | part of `make test` (`tests/test_aws_guard.py`) | Tier 0 | Every AWS target refuses before any aws/terraform call without credentials, and creating targets refuse without `AWS_CONFIRM=yes` (fake binaries; real AWS is never reachable from the test) | you; CI `test` job |

## What the tests cover

`make test` runs `pytest -q` over `tests/`. Groups, by file name:

- **Data and traces:** `test_schema`, `test_pipeline`, `test_samples`, `test_data_integrity`,
  `test_no_leakage` (no session crosses TRAIN/VAL/TEST), `test_vocab`, `test_synthetic`,
  `test_nan_timing`, `test_replayer`.
- **Predictor and simulator:** `test_graph`, `test_predictors`, `test_lgbm_ranker`, `test_eval`,
  `test_decide`, `test_lookahead`, `test_cold_start_config`, `test_service*`, `test_load`,
  `test_results_summary`.
- **Gateway, controller, CLI:** `test_gateway*`, `test_controller`, `test_concurrent_check`,
  `test_early_connect`, `test_scenario_run`, `test_cli`.
- **Chart, tools, infra:** `test_chart_matches_tool_map`, `test_monitoring`, `test_live_chart`,
  `test_tools_mcp`, `test_tool_map_coverage`, `test_aws_infra`, `test_aws_guard`,
  `test_e0_cold_start`.
- **Agent and console:** `test_agent`, `test_console`, `test_live_tasks`, `test_skyair`,
  `test_truth`.
- **Scripts:** `test_doctor` (fake PATH: no Docker, Docker stopped, low memory, no Python),
  `test_fetch_models` (local fake release: missing release, bad checksums, slug and origin
  rules), `test_training_data`.

Skipped tests need files that are not committed: the datasets (`data/processed/`) or the real
predictor bundles (`models/`). pytest prints the reason for each skip with `pytest -rs`.

## CI

[`.github/workflows/ci.yml`](../.github/workflows/ci.yml), on every push to `main`, every pull
request and by hand (`workflow_dispatch`):

- **`test`**: Python 3.12 from `requirements.lock` (pip cache), `tools/check_sizes.py`,
  `ruff check .`, `pytest -q`, `tools/doctor.sh test`.
- **`e2e`**: a clean `ubuntu-latest` runner. Frees disk, installs pinned kind v0.33.0,
  kubectl v1.37.2 and Helm v4.3.1 (cached), `make setup`, `make doctor DOCTOR_MODE=demo`, then
  `demo/ci_e2e.sh`. No Ollama, no GPU, no network LLM: the agent uses the scripted LLM. The
  `.demo/` logs are uploaded as the `e2e-demo-logs` artifact. Docker layers are not cached, so
  every image is built from scratch.

## Running the end-to-end check next to an existing cluster

`demo/ci_e2e.sh` and every kind script take the cluster, registry and ports from the
environment, so a second cluster can run beside a default `skypiea` one:

```bash
CLUSTER=skypiea-rt REG_PORT=5002 REG_NAME=kind-registry-rt PF_BASE=28080 GRAFANA_PORT=3300 \
  demo/ci_e2e.sh
CLUSTER=skypiea-rt REG_NAME=kind-registry-rt PF_BASE=28080 make demo-clean
```

## Measured

On the WSL2 laptop of [`DEMO.md`](DEMO.md) (Prerequisites), 2026-11-06, branch
`feat/redteam-ready`:

| What | Result |
|---|---|
| `make test` (in this checkout) | 461 passed, 6 skipped in 129 s (pytest's own timer) |
| `make test` on the final branch code | 472 passed, 6 skipped in 148 s |
| `demo/ci_e2e.sh` again on the final code, cluster `skypiea-rt` already up | PASS in 282 s (`make demo` 137 s, scripted agent 145 s) |
| `make setup` / `make test` in a fresh clone (new folder, clean venv) | 39 s / 474 passed, 2 skipped in 135 s |
| `demo/ci_e2e.sh` on a new cluster `skypiea-rt` (warm Docker build cache, all 16 tool images, platform and predictor rebuilt for a new tag) | PASS in 448 s: doctor 1 s, `make demo` 301 s, scripted agent 145 s, all 16 tool Deployments at 0 replicas 1 s later |

The `make demo` table of that run (kind, SYNTHETIC sessions: a functional demo, not results):

| Scenario | Policy | sessions | calls | ok | cold rate | added p50 ms | added p99 ms | idle pod-s |
|---|---|---|---|---|---|---|---|---|
| A | scale_to_zero | 8 | 19 | 19 | 94.7% | 2,773 | 4,813 | 47 |
| A | predictive | 8 | 19 | 19 | 21.1% | 0 | 2,749 | 106 |

Earlier timings (fresh clone `make setup` 35 s, `make demo` 270 s on a new cluster, `make live`
44 s and 2 min 47 s) are in [`DEMO.md`](DEMO.md) ("Start and stop" and "Resources").
