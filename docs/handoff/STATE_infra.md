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

## Replayer, CLI, monitoring, AWS plan, root fixes (untimed): done

Commits `a8567cc` (replayer, CLI, gateway session introspection), `0b24053` (monitoring),
`c1aecf2` (AWS plan doc + Terraform cost guards), plus the root README/Makefile commit.

- Replayer `python -m replayer.replay --config replayer/configs/<x>.yaml`: open loop across
  sessions, real think gaps and concurrent batches inside a session; `results` mode refuses
  scaled gaps; per-call CSV (session, tool, cold, added_latency_ms, hold_ms, predicted,
  policy, commit). Functional run on kind: 4-6 sessions, all calls ok (not results).
- CLI `bin/skypiea --context kind-skypiea | --kubeconfig ~/.kube/skypiea-aws`: status, policy
  get/set, sessions, trace, explain; via the Kubernetes API proxy (no port-forward, no secrets).
  Checked on kind.
- Monitoring (chart, default on, off for E0): Prometheus scrapes tools/gateway/controller;
  Grafana dashboard provisioned (cold rate, prewarm hit rate, p50/p99 added latency, hold,
  warm pool, idle pods/pod-seconds, predictor latency/errors, scale events). Checked on kind:
  3/3 targets up, dashboard served, Grafana -> Prometheus query works. Grafana needed 512Mi.
- AWS (2026-11-03): `terraform plan -out=tfplan` ran with the user's credentials:
  **Plan: 50 to add, 0 to change, 0 to destroy**. NOT applied (waiting for "APPLY").
  The redesign:
  - SSM Session Manager only: no inbound rule, no SSH or key pair; egress is TCP 443 only.
  - Least-privilege inline IAM: ECR pull on skypiea/*, S3 rw on our bucket, minimal SSM.
  - IMDSv2; 20 GB encrypted gp3, sized from measured images (peak about 10.7 GB < 85% GC
    threshold).
  - Bucket: private, SSE-S3, TLS-only.
  - No auto-stop (it breaks E0): calendar reminder at apply instead.
  - `make aws-destroy` empties bucket + ECR, then destroys.

  E0 node commands go over SSM (`infra/aws/scripts/node-run.sh`). The operator IAM policy the
  user must attach is `infra/aws/operator-policy.json`. Prices and open questions are in
  `docs/aws_plan.md`: about $0.108/h, $2.60/day.
- Root: Makefile includes `mk/infra.mk` (`make cluster/deploy/clean-cluster` now kind + helm);
  README layout, setup (kind + AWS), quick reference, roadmap, docs links updated;
  `deploy/k8s/` and `infra/azure/` removed.

## Open decisions for the user

- Whether light tools need heavier, more realistic startup (decide after E0).
- Confirm the predictor contract v1 with the model window (docs/predictor_contract.md).
- CI (.github/workflows, not changed on this branch) installs only pytest ruff numpy pandas
  pyarrow pyyaml: the gateway/controller/replayer/CLI/MCP tests skip there (importorskip).
  To run them in CI, add `pip install -r requirements-infra.txt` and a helm binary.

## v1.1 (branch feat/agent, from main fa2437d): demo + live-LLM agent — IN PROGRESS

Running log, newest last (the master task runs unattended; see docs/handoff/AGENT_REPORT.md at the end).

- STEP 0 started 2026-11-05: `make demo` (mk/demo.mk, demo/run.sh). A fresh clone has no
  data/processed, so `python -m demo.prepare` builds the SYNTHETIC prd demo bundle and the PRD
  Scenario A sessions from the committed samples (data/samples) in ~5 s. Chart fix: Grafana
  plugin preinstall disabled (it filled the 512Mi emptyDir; 740 evicted pods on kind).
  kind note: after a Docker Desktop resume the API port forward returned EOF; `docker restart
  skypiea-control-plane skypiea-worker` fixed it.
- STEP 0 done (2026-11-05 21:47 local): `make demo` ran end to end on the existing cluster in
  243 s (warm build cache): Scenario A, 8 SYNTHETIC sessions, scale_to_zero 18/19 cold vs
  predictive 4/19 cold, all 19/19 calls ok both times (functional demo, not results).
  docs/DEMO.md written; fresh-clone verification pending (STEP 3).
- STEP 1 done: agent/ (llm, mcp_client, loop, tasks A/B, run, platform harness, summarize,
  probe), tests/test_agent.py (15 offline tests), `make agent-demo` (scripted: 6/6 sessions
  followed the graph, all calls ok), docs/AGENT.md. No new dependency (httpx). Design choice
  made after a 1-session smoke test and before any measured run: a fixed simulated-user
  "confirm, continue" reply (max 2 per session) when the 8B model answers in text.
- STEP 2 done: `make agent-live` 21:54-22:04Z, 6/6 cells rc 0, predictor a7642d03dd10 (shipped
  prd bundle), llama3.1:8b, N=10 per cell -> results/agent_runs/ (summary.csv/.md).
- STEP 3 done: fresh clone ~/skypiea-fresh: setup, test 352 passed, ruff, check-sizes, make demo
  (new cluster, 270 s), agent-demo, demo-failures PASS. README Demo section.
- FINAL: docs/handoff/AGENT_REPORT.md. Cluster left up (demo bundle prd-demo-25c176b8d6ba,
  gateway serves the model), port-forwards started from ~/skypiea-infra.

## v1.2 Live Console (branch feat/live-console, from feat/agent) — IN PROGRESS

- STEP 1 done (vocabulary): console tasks `airline` (support profile, tau2-bench) and
  `codefix` (coding profile; manifest source tracelab, policy tuned on TraceLab + Copilot:
  label says so) in agent/tasks/live.py; every LLM tool maps to a class in the real profile's
  vocabulary (configs/live/predictor_vocab.json, generated from image af000231bc7b; test
  tests/test_live_tasks.py). Coverage + abstain table in docs/LIVE_CONSOLE.md. Tools:
  user_lookup `with_reservations`, travel_booking `detail`/`flight_no` (opt-in; defaults
  unchanged; image tag changes because tools/ changed).
- STEP 3b gateway part done: POST /v1/hint behind `platform.gateway.turnHint` /
  SKYPIEA_TURN_HINT (default off: 404, nothing else changes); contract v1.1 optional
  `trigger`/`turn` (docs/predictor_contract.md); reason `hint:<ver>`, metrics
  skypiea_gateway_hints_total / _hint_warms_total, header X-Skypiea-Warm-Source (hint on
  only). Tests: tests/test_gateway_hint.py, tests/test_service_hint.py.
- STEP 2 done: demo/live_stacks.sh deploys skypiea-base (stub, scale_to_zero, no monitoring)
  and skypiea-pred (real predictor af000231bc7b, profile support|coding, turn hint enabled,
  Prometheus/Grafana over both namespaces, live dashboard). First deploy 2 min 47 s (images
  a5386d4 built with warm cache). Idle, both stacks: kind control-plane 0.72 GiB, worker
  1.09 GiB, registry 60 MiB. Hint verified on kind: turn 1 -> support profile -> warm
  user_lookup (p 0.50); baseline answers 404.
  NOTE: at 23:02 the kind cluster AND the registry container did not exist any more (all
  three containers were re-created by deploy/kind/up.sh); cause unknown (not this session).
  The v1.1 skypiea-tools demo stack is therefore gone; `make demo` recreates it.
- STEP 3/4 first version: console/ (app, engine, poller, stacks, llm_stream, static UI);
  mirrored calls (concurrent|sequential), predictive result feeds the LLM, results compared;
  first live autopilot airline run worked end to end (3 calls each stack, all ok). Found:
  llama3.1 sometimes writes tool calls as JSON text -> flagged recovery (tool_call_from_text);
  first starts on a NEW cluster include image pulls -> `live_stacks.sh up` pre-pulls the tool
  images onto the worker (every start = cached start, as E0 "cached").
- STEP 7 first pass found a FAIRNESS BUG in the mirror (wait for both stacks -> the predictive
  stack got the slow baseline's time as lead time). Two runs discarded (kept, marked INVALID,
  in results/live_console_demo/invalid_wait_for_both_mirror/). Fixed with the gap-matched
  baseline lane (1fe5fdf), tested. Verification restarts from a new fresh clone.
- STEP 7 done: fresh clone ~/skypiea-live-fresh2 verified (setup, 389 tests, ruff, sizes,
  make live 43.5 s, 4 conversations hint off/on, predictor-kill PASS, replay, policy, export,
  make live-clean). More fixes found and made: lane submit timing (c4c8e9c), record order
  (d17b6d9), autopilot stop semantics (9c701f9), static no-cache. Runs:
  results/live_console_demo/ (demo runs, not results). Backup session: docs/live_backup/.
- FINAL: docs/handoff/LIVE_CONSOLE_REPORT.md. Stacks + console left running from
  ~/skypiea-infra (`make live`, 2026-11-06), conversation reset.

## v1.3 lead time (branch feat/console-leadtime, from main d15dc2b) — IN PROGRESS

- Lookahead option: DecideConfig.lookahead (default 1; every shipped profile/bundle
  unchanged; tests/test_lookahead.py). lookahead=2 warms the most likely tool of the batch
  after next (graph horizon-2 table), now, default TTL, one copy, if new and above threshold.
  Pre-registered adoption rule (written before any result): adopt as a labelled variant for a
  profile only if at the shipped threshold its VAL equal-idle margin over the lookahead-1
  curve is > 0 with the 95% session-bootstrap CI above 0, and its margin over keep-alive is not
  below lookahead 1's. TEST once afterwards, reported regardless.
- Lookahead VAL done (results/sim_lookahead_val.md): support +0.00 vs la1 (all extra classes
  map to travel_booking, already warmed); coding la2 vs keep-alive +3.02 [2.01, 4.00] < la1
  +3.56 [2.66, 4.58], la2 idle outside the la1 curve. Rule: NOT adopted, no variant. TEST once
  next. Data: ~/skypiea-model/data/processed (read-only).
- Console mapping (configs/live/console_tool_map.yaml) via SKYPIEA_CLASS_OVERRIDES / chart
  platform.classOverrides (default empty, default render identical); search gets opt-in
  flight args; predictor PREDICTOR_DECIDE_OVERRIDES (lookahead only, after golden check).
- Lookahead TEST (once): same picture (results/sim_lookahead_test.md). la1 TEST points equal
  the README's published simulator rows (harness cross-check). No profile variant added.
- Portable models: bundles 4.0 MB, archive 1.0 MB (docs/PORTABLE_MODELS.md); make
  fetch-models (no default URL). NOT published/committed: user decision.
- Demo grid done (36/36 conversations, results/live_console_leadtime/tables.md). Typing hint:
  no first-call gain (typing 39-43 s > TTL 35 s), more idle; Enter hint: no gain; lookahead 2:
  coding better waiting, airline worse; Skypiea more idle in all 12 cells. Live typed
  conversation not done (needs a human). Early connect: code only, measurement skipped per
  user's deadline. FINAL: docs/handoff/LEADTIME_REPORT.md, VIVA.md 4.8. Console left running.
