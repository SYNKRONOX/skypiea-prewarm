# Changelog

Each entry names the tag or branch it describes. Numbers are in the files linked from
[`README.md`](README.md); none are repeated here.

## Unreleased: red-team fixes (branch `fix/redteam-2`, not merged)

Every red-team issue #2-#27 has a commit here or on `fix/redteam-1` (#7). Issues stay open
with `retest-requested` until the reporting team's retest passes. Verified locally with
`make review` (Tier 0 + Tier 1 PASS), `make live` (both stacks) and the scenario suite;
nothing was applied to AWS. Triage: `docs/handoff/TRIAGE_TABLE.md`.

- **Admin token on the remaining mutating endpoints.** Stub predictor `POST /admin/policy`
  (#27) and controller `POST /v1/warm` (#23, #5) need `X-Admin-Token` (Secret `skypiea-admin`,
  now mounted into the stub and the controller). Every caller sends it: CLI, Live Console,
  demo scripts, scenario/replay/concurrent checks, the agent, the AWS replay Job.
  `demo/session_auth_check.sh` (Tier 1) now allows exactly these three holders of the Secret
  (gateway, controller, stub predictor) and fails on any other workload.
- **Input validation.** Stub policy params validated at set time, `/decide` fails open, per-
  session state LRU-capped (#27); every `/v1/warm` field bounded, fixed metric labels, the
  refused list drained (#23); predictor contract bounds `start_in_ms`, `ttl_s`, decisions (#12);
  gateway body limit 1 MiB with 413 before buffering (#15).
- **Gateway.** Sliding 200-call history window plus `history_total` (decision P3), so long
  sessions keep prewarming past call 1,000 (#14); no re-send of a `tools/call` that may have
  reached the tool (-32003, #16); separate connection pools for tools, predictor, controller
  (#18); cold calls fail fast (-32004) when the controller reports failing scale-ups (#4).
- **Controller.** Timeouts on every Kubernetes call, the loop survives a hung patch,
  `/healthz` 503 when stuck, livenessProbe (#20); `/v1/state` reports `scale_failing` (#4).
- **FR-5.** Histogram `skypiea_gateway_call_latency_seconds{tool,cold}` and a Grafana panel
  "Per-call latency, cold vs warm" (#26).
- **AWS (static only, the live account was not changed).** `ec2:CreateTags` only on create
  with `project=skypiea` (#11); node role only under the `skypiea-node-boundary` permissions
  boundary, which the operator cannot remove (#13).
- **SkyAir / Live Console.** Same-flight change refused, no fee (#21); promo on the fare only
  (#22); seats follow bookings (#17); CSV exports neutralise formula cells (#6); a negated yes
  is not consent (#9); a confirmed write must match the quoted target (#10); quotes expire
  (#19); text-written tool calls obey a turn's tool suppression (#25).
- **CLI / tooling.** `skypiea status` reports the predictor the gateway actually uses, `policy
  set` refuses when it would have no effect (#8); clean one-line errors for a wrong
  `--context` or a missing config (#24); `make doctor` (mode all) warns instead of failing
  without Tier 2 bundles (#2); base image digest refreshed (apt 2.6.2, #3).

## Security fixes (branch `fix/redteam-1`, merged to `main`)

- **Gateway session introspection needs the admin token** (issue #7, GHSA-r23c-9c4p-2vxp).
  `GET /v1/sessions` and `GET /v1/sessions/{sid}` were unauthenticated: any caller on the
  cluster network could list session ids, read calls and decisions, and use an id to append
  calls (`POST /mcp`) or end the session (`DELETE /mcp`). They now need `X-Admin-Token`
  (`ADMIN_TOKEN`; unset = disabled, 403). The chart generates Secret `skypiea-admin` and
  passes it only to the gateway; `skypiea sessions/trace/explain` and the Live Console read it
  with the operator's kubeconfig. Gateway-assigned `Mcp-Session-Id`s are 256-bit random
  (`secrets.token_urlsafe(32)`, was `uuid4().hex`), and so is the one id the Live Console
  sends to both stacks (was `uuid4().hex` too). `make review` Tier 1 checks it on the cluster
  (`demo/session_auth_check.sh`: the gateway's own 401 for no token and a wrong token, and
  only the gateway references the Secret). MCP protocol unchanged.
- **Opt-in NetworkPolicy** (`networkPolicy.enabled`, default `false`): ingress to tool and
  predictor pods only from the gateway and the controller. Enforcement NOT verified on kind.
- **Live Console refuses cross-site requests** (REQUESTS_W2 R9): Host allowlist, same-origin
  check and a required `X-Skypiea-Client` header on every state-changing request, no CORS
  headers. Scripts that drive the console send Origin and the header.
- Docs: `SECURITY.md` lists every endpoint with its auth requirement and the trust boundary;
  `docs/KNOWN_ISSUES.md`, `docs/PRD_STATUS.md` (FR-7, NFR-6) say what was exposed before.

## v1.2: lead time, the support console, red-team readiness

**Red-team readiness (branch `feat/redteam-ready`):**

- `make doctor`: OS/WSL, Docker running and its memory, kind, kubectl, Helm, Python 3.12, disk,
  RAM, ports, bundles, optional LLM endpoint; an exact fix per failure. `make demo` and
  `make live` run it first and stop before changing anything.
- AWS safety: every AWS target refuses before any aws/Terraform call without the CLIs,
  credentials and local state; creating targets also need `AWS_CONFIRM=yes`.
- `make fetch-models`: release URL from a configured slug (`MODELS_REPO`, default
  `SYNKRONOX/skypiea-prewarm`), GitHub-origin fallback, clearer "not available" message.
- CI: new `e2e` job (kind on a clean runner, `make demo`, scripted agent, tools back to 0).
- Cluster, registry and ports overridable for every kind script (`CLUSTER`, `REG_PORT`,
  `PF_BASE`, `GRAFANA_PORT`, ...); clear messages when a port is taken or the cluster stops.
- `make help` (default goal), `make clean-images`, `make secret-sweep`.
- Docs: README rewritten as an install-and-use guide in tiers with Known limitations,
  troubleshooting and FAQ; `docs/TESTING.md`; generated `docs/training_data.md`; issue
  templates, `CONTRIBUTING.md`, `SECURITY.md`. Self red-team log:
  `docs/handoff/SELF_REDTEAM.md`.

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

**Support console (branch `feat/console-support`, merged into `main`):**

- Skypiea Air: a fictional, seeded mock airline database inside the console, with session
  tools (verification, confirmation, refund rules) and demo personas.
- Skye, the support agent on the local 8B model. The gateways receive only the tool class with
  empty arguments; no customer data goes to the pods.
- The typing hint is re-sent at Enter.
- `make fetch-models` downloads the GitHub Release v1.2 asset (predictor bundles +
  `SHA256SUMS`), verifies the archive and every file, and says clearly when the release is not
  published. `docs/MODELS_ATTRIBUTION.md`.
- Phase B (UI): a customer-facing chat view and a truth panel that shows the session
  database's real state, so claims by the model can be checked on screen
  ([`docs/LIVE_DEMO.md`](docs/LIVE_DEMO.md)).
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
