# feat/infra handoff state (2026-11-02)

Branch `feat/infra` (worktree `~/skypiea-infra`). Local commits only: nothing pushed or merged.
Raw chat exports live next to this file as `*.txt` and are git-ignored.

## Done

| Commit | What |
|---|---|
| `46ee658` | 16 MCP tool servers (`tools/`), `tools/tool_map.yaml`, Helm chart `charts/skypiea`, kind up/down, E0 harness + in-cluster probe, Terraform in `infra/aws` (validated only), `mk/infra.mk`, `docs/infra.md`, tests |
| `c7b3ab6` | E0: "uncached" removes all tool images; probe on an alpine base (shares no layers with tools) |
| `5d5074d` | E0 probe: 100 ms TCP gate before each first-call attempt, per-attempt log, removes old-tag images |
| `b444bea` | E0 probe: MCP client warmed before t0 (client import cost kept out of first_call) |
| `c29a39f` | `results/tool_startup.csv`: image sizes and plain-docker startup, n=5 per tool |
| `8ddcf55` | Image tag = last commit that changed an image input (`tools/`, `experiments/e0_probe/`) |
| `8a1d30d` | `.gitignore`: `docs/handoff/*.txt` |

Key facts:

- MCP SDK `mcp==2.3.0` (`MCPServer`), Streamable HTTP, stateless, protocol 2026-07-28. A client
  `call_tool` is 2 POSTs (`tools/call`, `tools/list`); no initialize, no session id.
- 16 servers; named servers cover TraceLab 98.0%, Copilot 99.2%, tau-bench 95.4%, tau2 99.0%
  of calls (`make tool-map-coverage`). `edit_file` = write_file; escalate and planning go to
  `post_comment`; airline classes to `travel_booking`; web and BFCL domains to the fallback.
- Image sizes: light 265 MB, medium 470 MB, heavy 624-634 MB, probe 316 MB.
  Plain-docker app startup (median): light 710-870 ms, medium 990-1060 ms, heavy 1480-1610 ms.
- kind cluster `skypiea` (1 control-plane + 1 worker, registry `localhost:5001`) is running with
  the chart deployed at images `8ddcf55`: all 16 tools at 0 replicas, e0-probe at 1. Verified
  0 -> 1 -> MCP call through the Service -> 0 for `policy_check` and `edit_file`.

## E0 status

**kind: done** (commit `c0deb28`; data `f72afa0`, images `8ddcf55`, phases derived at `620a6ea`).
Files: `results/e0_kind{,_p3}_{runs,summary}.csv`, `results/e0_kind{,_p3}.png`.

| kind (local registry = pull lower bound) | cached median / p95 | uncached median / p95 |
|---|---|---|
| sequential, n=10 (320 runs) | 2544 / 4219 ms | 4070 / 6326 ms |
| 3 tools at once, n=10 (360 runs) | 2936 / 4035 ms | 4730 / 6940 ms |

0 noisy, 0 failed, 0 cached/uncached mismatches; outliers kept (2 + 6, all < 1.7x their median).
Every tool is above 1 s in both modes. Biggest cached phase: app_init; uncached: image_pull.
Caveat: the sequential run's app_init was ~35% slower than plain docker and than the later
parallel run (server-reported too); WSL load average was low, Windows host load is not visible.

E0 harness fixes since the first commit: uncached removes all tool images (shared layers);
probe on an alpine base; 100 ms TCP gate; client warm-up before t0; pull phase includes queue
wait; pull time recovered from the kubelet event in report mode.

Next for E0: AWS (Terraform plan/apply only after the user confirms the account), n=30 then
n=100, plus `--parallel 3` n=10.

## Simulator input and replica ownership

- `configs/cold_start.yaml` (commit `c21a567`): generated from the E0 kind CSVs by
  `python -m experiments.e0_write_cold_start --env kind`; `environment: kind`, marked "lower
  bound; replace with AWS". Schema: `docs/cold_start_schema.md`. A test checks that the file
  equals the generator output. E0 run settings now live in `experiments/e0_config.yaml`.
- Replicas (commit `96c3b80`): the controller owns them. The chart creates Deployments at 0
  and `helm upgrade` keeps the live count (Helm `lookup`); verified on kind.

## Gateway + controller (Task B): done, functional on kind

Commits `71fd418` (code + tests), `f82e66b` (chart `skypiea`, platform image, replay check),
`60fc6d7`, `47fe884`, `fe8d46e`, `18a711f` (fixes found on kind + docs). Design:
`docs/gateway_controller.md`; draft contract for the model window: `docs/predictor_contract.md`.

- `helm install skypiea charts/skypiea` (`make deploy-kind`): 16 tools at 0 + controller (kopf),
  gateway, predictor placeholder; all ClusterIP; namespaced least-privilege RBAC.
- Gateway: POST /mcp, sessions, class -> server, 150 ms hold, cold path, direct-to-pod routing,
  predictor off the critical path with FR-4 fallback, NFR-6 purge, metrics.
- Controller: TTL + 5 s purge from last use, in-flight floor, 5 s dwell, caps 3/tool and 20
  total, idempotent, rebuild from annotations; HTTP API /v1/warm, /v1/inflight.
- Tests: 109 passing (unit + failure injection + official MCP client end to end).
- Kind: Scenario A + B under all 6 policies pass the functional checks; live injection
  (predictor dead, controller restart, stuck Pending) behaves as designed. Not results.
- Stubbed: predictor policies are placeholders (`gateway/predictor_stub.py`) until the model
  branch's `predictors/policies.py` + service are merged; the real service plugs in via
  `platform.predictor.url` with no gateway change.
- E0 must run without the controller: `make deploy-kind-e0` / AWS `PLATFORM=false`.

## Open decisions for the user

- Whether light tools need heavier, more realistic startup (decide after E0).
- Root Makefile: add `include mk/infra.mk`; README still says kind has 2 workers (now 1),
  and references `charts/skypiea-tools` / `make deploy` (now `charts/skypiea`, `make deploy-kind`).
- Confirm the predictor contract v1 with the model window (docs/predictor_contract.md).
