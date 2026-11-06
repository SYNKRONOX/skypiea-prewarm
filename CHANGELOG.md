# Changelog

Each entry names the tag or branch it describes. Numbers are in the files linked from
[`README.md`](README.md); none are repeated here.

## v1.2: lead time and the support console (in progress, merged if ready)

Untagged. Not published as a release at the time of writing.

**Lead time (merged into `main`, 9d62db2):**

- Typing-start and Enter-time turn hints in the console (modes off / Enter / typing), with
  per-turn records of the lead time.
- Decider lookahead option (default 1; the shipped profiles are unchanged). Lookahead 2 was
  evaluated on VAL and once on TEST and **not adopted** under the pre-registered rule
  (`results/sim_lookahead_val.md`, `results/sim_lookahead_test.md`).
- Console-only tool mapping (`configs/live/console_tool_map.yaml`), so the airline tools run on
  distinct servers. `tools/tool_map.yaml`, used for every AWS result, is unchanged.
- Gateway early connect (off by default, `platform.gateway.earlyConnect`) and an A/B harness.
  **Not measured.**
- Lead-time demo grid: 36 autopilot conversations, **demo runs, not results**
  (`results/live_console_leadtime/tables.md`, `docs/handoff/LEADTIME_REPORT.md`).
- Portable models proposal (`docs/PORTABLE_MODELS.md`) and `make fetch-models` (no default URL
  in this step).

**Support console (branch `feat/console-support`, in progress; merged if ready):**

- Skypiea Air: a fictional, seeded mock airline database inside the console, with session
  tools (verification, confirmation, refund rules) and demo personas.
- Skye, the support agent on the local 8B model. The gateways receive only the tool class with
  empty arguments; no customer data goes to the pods.
- The typing hint is re-sent at Enter.
- `make fetch-models` downloads the GitHub Release v1.2 asset (predictor bundles +
  `SHA256SUMS`), verifies the archive and every file, and says clearly when the release is not
  published. `docs/MODELS_ATTRIBUTION.md`.
- Phase B (UI, in progress): a customer-facing chat view and a truth panel that shows the
  session database's real state, so claims by the model can be checked on screen.
- Known issue: the 8B model can claim actions it did not take.

## v1.1: demo, live agent, Live Console (tag `v1.1`, 2026-11-06)

- `make demo`: the whole stack on kind from a fresh clone (profile `prd`, SYNTHETIC Scenario A,
  scale-to-zero vs predictive), `make demo-failures` (predictor stopped, controller killed; the
  calls still succeed), `make demo-clean` ([`docs/DEMO.md`](docs/DEMO.md)).
- Tool-calling agent through the gateway's `POST /mcp` only, with a scripted or any
  OpenAI-compatible LLM; `make agent-demo`, `make agent-live` and live runs with llama3.1:8b
  on kind (`results/agent_runs/`, [`docs/AGENT.md`](docs/AGENT.md)).
- Live Console (`make live`): baseline and predictive stacks in one kind cluster, mirrored
  calls, pod grids, predictor panel, think window, turn-start hint (off by default; gateway
  `POST /v1/hint`, contract v1.1), predictor kill, Grafana live dashboard
  ([`docs/LIVE_CONSOLE.md`](docs/LIVE_CONSOLE.md)).
- Console verification runs, **demo runs, not results** (`results/live_console_demo/`), and a
  recorded backup session (`docs/live_backup/`).

## v1.0-submission (tag `v1.0-submission`, 2026-11-05)

- Gateway, kopf scale-to-zero operator (cap of 2 scale-ups per node), predictor service
  (Markov/n-gram graph + LightGBM re-ranker + warm policy; profiles `support`, `coding`,
  SYNTHETIC `prd`), CLI.
- 16 MCP tool servers, Helm chart with Prometheus and Grafana, trace pipeline over five public
  datasets, discrete-event simulator, replayer, Terraform for one EC2 k3s node.
- Results: E0 cold starts on kind and AWS, AWS Scenario A/B runs with session-bootstrap CIs,
  simulator equal-idle margins and oracle, next-tool accuracy, predictor load test, SYNTHETIC
  PRD runs ([`docs/results_summary.md`](docs/results_summary.md)).
- Limits ([`docs/limits.md`](docs/limits.md)) and every quoted number's source
  ([`docs/numbers_and_sources.md`](docs/numbers_and_sources.md)).
