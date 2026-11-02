# feat/infra handoff state (2026-11-02)

Branch `feat/infra` (worktree `~/skypiea-infra`). Local commits only: nothing pushed or merged.
Raw chat exports live next to this file as `*.txt` and are git-ignored.

## Done

| Commit | What |
|---|---|
| `46ee658` | 16 MCP tool servers (`tools/`), `tools/tool_map.yaml`, Helm chart `charts/skypiea-tools`, kind up/down, E0 harness + in-cluster probe, Terraform in `infra/aws` (validated only), `mk/infra.mk`, `docs/infra.md`, tests |
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

- **Not run yet** (timed runs wait for the user to pause model training).
- Only smoke runs exist (n=2, 4 tools; untracked `results/e0_kind*`, not results).
- Next commands, once the user says go:
  `python -m experiments.e0_cold_start --target kind --n 10` (about 1 h, estimate), then
  `python -m experiments.e0_cold_start --target kind --n 10 --parallel 3` (about 30 min).
- Then the AWS phase (Terraform plan/apply only after the user confirms the account), E0 on AWS
  at n=30 and n=100.

## Next: gateway + controller

- Gateway/shaper: route class -> server via `tools/tool_map.yaml` (`tools.common.toolmap.load()
  .server_for(cls)`, Service name = server with `_` -> `-`, port 80, path `/mcp`); hold calls up
  to 150 ms when a pod is about to be Ready.
- Controller (kopf): scale the plain Deployments (label `skypiea.io/tool=<server>`) between
  0 and N. The chart sets `replicas: 0`, so a `helm upgrade` resets replicas to 0; the controller
  must own the replica count (or the chart must drop `replicas`) before both run together.
- Readiness probe: `/healthz`, period 1 s, failure threshold 60. E0 shows the Ready -> Service
  routing lag is part of the cold start.

## Open decisions for the user

- Whether light tools need heavier, more realistic startup (decide after E0).
- Root Makefile: add `include mk/infra.mk`; README still says kind has 2 workers (now 1).
