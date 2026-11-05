# Skypiea infrastructure: tool servers, clusters, E0

Owner: Ch. Dhanunjaya. Make targets are in [`mk/infra.mk`](../mk/infra.mk) (included from the
root Makefile). Laptop deps: `pip install -r requirements.txt -r requirements-infra.txt`.

## Tool servers (`tools/`)

- `tools/tool_map.yaml` is the single source of truth for tool class -> server (16 servers).
  Rule: per scenario source the named servers cover >= 95% of calls, the rest go to
  `generic_fallback`. Check: `python tools/tool_map_coverage.py <data/processed>`.
- MCP Python SDK **2.3.0** (`mcp.server.mcpserver.MCPServer`; FastMCP was renamed in 2.x),
  Streamable HTTP at `/mcp`, stateless, JSON responses. Clients pin protocol **2026-07-28**:
  every request is a self-contained POST with no `initialize` and no session id. The SDK's
  `call_tool` sends `tools/call` then `tools/list` (output-schema validation): 2 POSTs.
- Each server has `/healthz` (also reports process uptime and startup time) and `/metrics`
  (Prometheus), logs JSON lines, and takes `service_ms` (real CPU or file I/O work, not sleep).
- Startup tiers: light = python-slim + MCP SDK; medium = + pandas/numpy and a data file loaded
  at startup; heavy = + scikit-learn and a model trained at image build time, loaded at
  startup.

## Chart sizing and CPU contention

Requests: 875m CPU / 2.4 GiB for all 16 tools at 1 replica; 1325m / 3.9 GiB with 3 extra
copies of the 3 heavy tools, which fits one 2 vCPU / 8 GiB node. Each tool's CPU **limit is one
full core** so startup (CPU-bound imports and model loading) is not artificially throttled.

On the 2 vCPU AWS VM this means **heavy tools compete for CPU when several start together**:
two or three heavy pods importing scikit-learn at the same moment share 2 cores, so each
starts slower than alone. E0 therefore measures both one pod at a time (`--parallel 1`) and
3 pods scaled up at the same moment (`--parallel 3`, n=10).

## E0 noise control

The laptop is shared with model training. Before every timed run E0 records the 1-minute load
average, available memory and running containers of the machine that hosts the node (laptop
for kind, VM for AWS). With `--require-quiet` (default) it waits up to 60 s for
load1 <= 60% of the cores; if it does not drop, the run is kept and flagged `noisy`. Summaries
report medians over all runs and over quiet runs only.

**kind pulls from a local registry container**, so its image-pull phase is a **lower bound**
(no network distance). The AWS run pulls from ECR and gives the real number.

## Gateway, controller, replayer, CLI, monitoring

| What | Where | Docs |
|---|---|---|
| Gateway (one `/mcp` endpoint, 150 ms hold, predictor off the critical path) | `gateway/` | [gateway_controller.md](gateway_controller.md), [predictor_contract.md](predictor_contract.md) |
| Scale-to-zero controller (kopf) | `controller/` | [gateway_controller.md](gateway_controller.md) |
| Replayer (open loop, real gaps, per-call CSV) | `replayer/replay.py`, configs in `replayer/configs/` | module docstring; `results` mode requires gap/service scale 1 |
| CLI | `bin/skypiea` (`status`, `policy get/set`, `sessions`, `trace`, `explain`) | uses the kubeconfig only (Kubernetes API proxy) |
| Prometheus + Grafana | `charts/skypiea/templates/monitoring.yaml`, `files/skypiea-dashboard.json` | `make grafana-kind`; anonymous viewer; admin password in Secret `skypiea-grafana` |
| AWS | `infra/aws/` | [aws_plan.md](aws_plan.md), [infra/aws/README.md](../infra/aws/README.md) |

E0 runs without the controller and monitoring (`make deploy-kind-e0`; on AWS `PLATFORM=false`).
