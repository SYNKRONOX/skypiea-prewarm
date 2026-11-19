# Security policy

Skypiea is a course project (DS252, IISc). It is not production software and has no supported
releases beyond the tags in [`CHANGELOG.md`](CHANGELOG.md).

## Reporting a vulnerability

Please do **not** open a public issue with details. Use GitHub's private vulnerability report
(repository **Security** tab, **Report a vulnerability**) if it is enabled. If it is not, open
an issue titled `security: contact request` without any details, and a maintainer will reply
with a private channel.

Useful to include: the commit (`git rev-parse --short HEAD`), the component (gateway,
controller, predictor, console, chart, Terraform, scripts) and how to reproduce it.

## Trust boundary

- **Trusted: whoever holds a kubeconfig for the namespace** (the operator). They can read
  Secrets, port-forward, scale Deployments and call every endpoint below; the CLI and the Live
  Console act with exactly these rights and add none.
- **Not trusted: anything else on the cluster network.** Every Service is ClusterIP, so any pod
  in the cluster can open a connection to any of them. By default (no NetworkPolicy) the
  cluster network is the only barrier, so a compromised or hostile pod is the threat this
  section is about.
- **Not trusted: other web sites in the presenter's browser.** They can make the browser send
  requests to `127.0.0.1`; the Live Console must refuse them (below).
- **Session ids are capabilities.** The gateway implements the stateless MCP revision
  2026-07-28: a session is named only by its `Mcp-Session-Id`. Whoever knows an id can append
  calls to that session (`POST /mcp`) and end it (`DELETE /mcp`). This is the protocol and it
  was not changed. That is why **listing or guessing ids had to be impossible**: the
  enumeration endpoints now need the admin token, and the ids the gateway assigns carry 256
  bits from the OS CSPRNG (`secrets.token_urlsafe(32)`; before: `uuid4().hex`, 122 random
  bits).

## Every endpoint and what it requires

"In-cluster" means: no authentication, reachable by any pod on the cluster network (and by the
operator through a loopback port-forward). Nothing is exposed outside the cluster.

| Component | Endpoint | Requires | Notes |
|---|---|---|---|
| Gateway | `POST /mcp` | in-cluster; the `Mcp-Session-Id` is the session capability | MCP `tools/call`, `tools/list`, `ping`. No id: the gateway assigns one (256 bits). Body over 1 MiB (`maxBodyBytes`): 413 before it is buffered (issue #15). A call whose connection dropped after sending is never re-sent (-32003, issue #16). |
| Gateway | `DELETE /mcp` | in-cluster; the session's `Mcp-Session-Id` | ends that session |
| Gateway | `POST /v1/hint` | in-cluster; the session's `Mcp-Session-Id` | 404 unless `turnHint` is on; body `{"turn": <int>}` only, at most 1 MiB |
| Gateway | `GET /v1/sessions` | **admin token** (`X-Admin-Token`) | lists every session id. 403 if `ADMIN_TOKEN` is unset (disabled), 401 for a missing or wrong token. **Was unauthenticated before `fix/redteam-1`.** |
| Gateway | `GET /v1/sessions/{sid}` | **admin token** (`X-Admin-Token`) | a session's calls and predictor decisions (metadata). Same 403/401; the token is checked before the lookup, so a wrong token never reveals whether an id exists. **Was unauthenticated before `fix/redteam-1`.** |
| Gateway | `GET /healthz` | in-cluster | status and the number of live sessions (no ids) |
| Gateway | `GET /metrics` | in-cluster | counters labelled by tool class only (no ids) |
| Stub predictor (`skypiea-predictor`) | `POST /decide` | in-cluster | metadata in, warm decisions out; any internal error answers "nothing warmed" (200), never 500 (issue #27) |
| Stub predictor | `GET /admin/policy` | in-cluster | reads the baseline policy (read-only) |
| Stub predictor | `POST /admin/policy` | **admin token** (`X-Admin-Token`) | sets the baseline policy (CLI `skypiea policy set`, Live Console, demo scripts). 403 if `ADMIN_TOKEN` is unset, 401 for a missing or wrong token; params validated per policy, a bad body is 400 and never stored. **Was unauthenticated before `fix/redteam-2` (issue #27).** |
| Stub predictor | `GET /healthz` | in-cluster | |
| Real predictor (`skypiea-model`) | `POST /decide`, `GET /healthz`, `GET /metrics` | in-cluster | `docs/predictor_contract.md` |
| Real predictor | `POST /reload`, `POST /rollback` | admin token (`X-Admin-Token`, its own `ADMIN_TOKEN`) | the chart sets no `ADMIN_TOKEN` for it, so both answer 403 (disabled) |
| Controller | `POST /v1/warm` | **admin token** (`X-Admin-Token`) | start pods (the gateway sends its own token). Every field bounded (copies 0-10, ttl_s 0-86400, start_in_ms 0-600000, tool_class 1-128 chars); out of range is 400, never 500. **Was unauthenticated before `fix/redteam-2` (issues #23, #5).** |
| Controller | `POST /v1/inflight` | in-cluster | in-flight counts per tool (the gateway's heartbeat); only known tools are kept |
| Controller | `GET /v1/state`, `GET /healthz`, `GET /metrics` | in-cluster | pool state per tool (no session data); `/healthz` is 503 when the scaling loop is stuck, and the livenessProbe restarts the pod (issue #20) |
| Tool pods | `POST /mcp`, `GET /healthz` | in-cluster; with the opt-in NetworkPolicy, only the gateway and controller | |
| Live Console (127.0.0.1:8090) | every request | `Host` is `127.0.0.1:<port>`, `localhost:<port>` or `[::1]:<port>` | else 403 (DNS rebinding) |
| Live Console | `GET /`, `/events`, `/static/*`, `/api/state`, `/api/personas`, `/api/models`, `/api/report`, `/api/skyair/state`, `/api/export.csv`, `/api/export_turns.csv` | Host check only | other sites cannot read the answers (no CORS headers) |
| Live Console | `POST /api/chat`, `/api/autopilot`, `/api/story`, `/api/{reset,replay,policy,hint,typing,lookahead,cpm,mirror,predictor,model,demo}` | Host check **and** `Origin` equal to the console's own origin **and** a non-empty `X-Skypiea-Client` header | else 403. The page's JavaScript sends the header; `demo/console_run.py` and `console.bakeoff` send Origin and header (`console/xsite.py` `script_headers`). |

**The admin token.** The chart generates Secret `skypiea-admin` (key `admin-token`, 48
random characters) at install, keeps it across upgrades, and passes it **only to the gateway,
the stub predictor and the controller** (`ADMIN_TOKEN` from a `secretKeyRef`; never a tool pod
or the model); it is never in the repository or in values files
(`tests/test_chart_security.py`). The CLI (`skypiea sessions`, `trace`, `explain`,
`policy set`) reads it with your kubeconfig, or from `SKYPIEA_ADMIN_TOKEN`; the Live Console
reads each stack's Secret with `kubectl` (or `SKYPIEA_ADMIN_TOKEN_BASE` / `_PRED`); the demo
scripts (`demo/run.sh`, `demo/agent_demo.sh`) export it as `SKYPIEA_ADMIN_TOKEN` and the AWS
replay Job gets it from the Secret. None of them prints or stores it. Rotate it by
deleting the Secret and running `helm upgrade` again (then restart the gateway).

## Fixed on branch `fix/redteam-1`

- **Session enumeration and hijack (issue #7, GHSA-r23c-9c4p-2vxp).** Before this fix,
  `GET /v1/sessions` and `GET /v1/sessions/{sid}` were unauthenticated. Any caller on the
  cluster network could list every live session id, read each session's calls and predictor
  decisions, then use an id to append calls to that session (`POST /mcp`) and terminate it
  (`DELETE /mcp`). This broke the intent of FR-7 (session isolation) and NFR-6 (per-session
  data not exposed). Now both endpoints need the admin token and are disabled when none is
  configured; the gateway's own ids are 256-bit random (`gateway/app.py`,
  `tests/test_gateway_session_auth.py`; on a cluster: `demo/session_auth_check.sh`, a step of
  `make review` Tier 1).
- **Opt-in NetworkPolicy** (`networkPolicy.enabled`, default `false`;
  `charts/skypiea/templates/networkpolicy.yaml`): ingress to tool pods and predictor pods
  (`skypiea-predictor`, `skypiea-model`) only from the gateway and the controller. It renders
  with `helm template` and passes `helm lint` (tests in `tests/test_chart_security.py`).
  **Enforcement was NOT verified on kind**, and is not verified on any cluster: no cluster ran
  with it on. A CNI without NetworkPolicy support ignores these objects silently. Do not
  assume kind enforces them: the default kind network (kindnet) did not enforce NetworkPolicy
  in older kind releases; kind v0.24.0's release notes announce network-policy support via
  `kube-network-policies` and this repo is tested on kind v0.33.0, but we have not checked
  that it blocks traffic here. Check on your own cluster before relying on it. With it on, the
  API-server service proxy (`skypiea policy`), Prometheus scraping tool/model pods and the E0
  probe are refused unless you add them to `networkPolicy.extraFrom`; `kubectl port-forward`
  is not affected.
- **Live Console cross-site requests (REQUESTS_W2 R9).** Before this fix, a web page open in
  the presenter's browser could change console settings with a simple cross-site POST (kill
  the predictor, change hint, policy, model, reset), and a DNS-rebinding page could reach it
  under its own host name. Now the console checks Host on every request, and Origin plus the
  `X-Skypiea-Client` header on every state-changing request, and sends no CORS headers
  (`console/xsite.py`, `tests/test_console_security.py`).

## Fixed on branch `fix/redteam-2` (retest requested; issues stay open until retest passes)

None of these was run on a cluster in this branch (Tier 0 only; CI runs Tier 1).

- **Stub predictor admin endpoint (issue #27).** `POST /admin/policy` needs the admin token;
  params are validated at set time (a bad config is 400 and never stored); `/decide` fails open
  to "nothing warmed" instead of a persistent 500; per-session learning state is an LRU capped
  at 10,000 sessions (`gateway/predictor_stub.py`, `tests/test_predictor_stub_admin.py`).
- **Controller `/v1/warm` (issues #23, #5).** Admin token required; every field bounded (400,
  never 500); the warm-request metric label is a fixed set; the refused-capacity list is
  drained after counting, so caller input cannot grow controller memory
  (`controller/service.py`, `tests/test_controller_warm_input.py`). #5's amplification through
  the gateway's own demand warms stays bounded by the caps (`max_total`, per-node starts, TTL).
- **Gateway body limit (issue #15).** `POST /mcp` and `/v1/hint` refuse bodies over 1 MiB with
  413, from `Content-Length` before reading and while streaming
  (`tests/test_gateway_body_limit.py`).
- **No duplicate tool execution (issue #16).** A `tools/call` whose connection dropped after it
  was sent is never re-sent; the client gets JSON-RPC -32003 "the tool may have run; not
  retried" (`tests/test_gateway_no_replay.py`).
- **AWS operator policy (issues #11, #13; static change, the live account was NOT changed).**
  `ec2:CreateTags` only on create (`ec2:CreateAction`) with `project=skypiea`; the node role
  can be created / given inline policies only under the `skypiea-node-boundary` permissions
  boundary (`infra/aws/node-boundary-policy.json`), which the operator cannot remove or edit
  (`tests/test_aws_policy_tags.py`, `tests/test_aws_policy_boundary.py`). An account admin
  must create the boundary policy and re-attach the operator policy for this to take effect.
- **Controller availability (issue #20).** Timeouts on every Kubernetes call, a hung patch no
  longer freezes the loop, `/healthz` reports a stuck loop and a livenessProbe restarts it.
- **Predictor contract bounds (issue #12):** `start_in_ms` <= 600000, `ttl_s` <= 86400, at most
  64 decisions; anything else is off-contract and ignored (FR-4).
- **Separate connection pools (issue #18)** for tool calls, the predictor and the controller.
- **CSV exports (issue #6)** neutralise cells starting with `= + - @` (tab, CR).
- **Live Console write guard (issues #9, #10, #19, #25).** A yes with any negation is not
  consent; a confirmed write must target what was quoted (and an omitted target uses the
  quoted one); quotes expire on a decline, a new request or the next turn; a tool call written
  as text on a flagged turn (tools=[]) is never executed.
- **Base image (issue #3)** refreshed to the current `python:3.12-slim-bookworm` digest (apt
  2.6.2); images were not rebuilt or re-scanned in this branch.

## Still open (known, not fixed here)

- **The rest of the in-cluster surface is unauthenticated** (table above): any pod can call
  `POST /mcp` with its own sessions (body and caps bounded), report in-flight counts
  (`POST /v1/inflight`), call `/decide`, or read the stub's policy. The opt-in NetworkPolicy
  narrows who can reach tool and predictor pods; it does not cover the gateway or the
  controller.
- **Client-chosen session ids.** The gateway accepts any `Mcp-Session-Id` a client sends (the
  stateless revision has no `initialize` to assign one). The repo's agent uses the id the
  gateway assigns. The Live Console chooses one id per conversation and sends it to both
  stacks (so their calls line up); since `fix/redteam-1` it is `secrets.token_urlsafe(32)`,
  as strong as the gateway's (before: `uuid4().hex`, 122 random bits; found on the live
  cluster, `tests/test_console.py::test_conversation_session_id_is_unguessable`). The replayer (`replayer/replay.py`) sends `<run_id>-<8 hex>` (32
  random bits) and the scenario runner (`gateway/scenario_run.py`) `uuid4().hex` (122 random
  bits): fine for replays on a private cluster, but a client that wants its session protected
  must let the gateway assign the id.
- **The predictor receives the raw session id** in `/decide` (`docs/predictor_contract.md`);
  it is in-cluster and keeps nothing on disk (`make audit`).
- **The Live Console has no login.** The guard stops other web sites and DNS rebinding;
  anyone who can reach 127.0.0.1 on your machine as a local process can still use it. Keep it
  on loopback: it binds to `127.0.0.1` only (`console/app.py` `main()`,
  `tests/test_console_security.py::test_console_binds_to_loopback_only`) and every port-forward
  uses `--address 127.0.0.1`. No reverse proxy, no `ssh -R`, no tunnel, no `0.0.0.0`
  port-forward, no shared machine where other users can reach your loopback. Its data is a
  fictional mock world (`docs/SKYPIEA_WORLD.md`); the LLM runs locally (Ollama) unless you
  point `AGENT_BASE_URL` elsewhere.

More known gaps and their workarounds: [`docs/KNOWN_ISSUES.md`](docs/KNOWN_ISSUES.md).

## What the project already does

- **No inbound ports on AWS.** The EC2 node is reached only through SSM Session Manager; no SSH,
  no key pair, outbound TCP 443 only, IMDSv2 required, encrypted root volume, a least-privilege
  instance role ([`infra/aws/README.md`](infra/aws/README.md), [`docs/aws_plan.md`](docs/aws_plan.md)).
- **AWS targets refuse by default.** Every `make aws-*` target stops before any AWS or Terraform
  call without the CLIs, credentials and local Terraform state; creating targets also need
  `AWS_CONFIRM=yes` (`infra/aws/scripts/aws-guard.sh`, `tests/test_aws_guard.py`).
- **Local services listen on 127.0.0.1 only** (port-forwards, Live Console); all cluster
  Services are ClusterIP ([`dev.md`](dev.md)).
- **No secrets in the repository.** `make secret-sweep HISTORY=1` scans the tree and every
  commit for credential-like text, unmasked AWS account ids and secret-like file names, and
  prints file names only (`tools/secret_sweep.sh`). Terraform state, plans and kubeconfigs are
  git-ignored. On 2026-11-06 the sweep found nothing outside its allow-list.
- **Metadata only.** The predictor receives tool names, timings and batch structure, never
  prompts or arguments; `make audit` checks that no session data is kept
  ([`docs/predictor_contract.md`](docs/predictor_contract.md), [`docs/redteam.md`](docs/redteam.md)).
- **Model downloads are verified.** `make fetch-models` accepts https only (http for
  localhost tests), checks the archive against the release's `SHA256SUMS` and every file
  against `configs/live/models_files.sha256`, and refuses unsafe archive paths.

## Dependencies

Python dependencies are pinned in `requirements.lock` (and the image constraint files). We did
**not** run a vulnerability audit of the pins: no advisory database or `pip-audit` is available
offline in this repository, and none was added. To check them yourself:
`pip install pip-audit && pip-audit -r requirements.lock` (this sends the package list to the
PyPI/OSV advisory services).
