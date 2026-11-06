# Skypiea Live Console

A presenter types into a chat; a local LLM (Ollama) answers and calls tools; every tool call
runs on **two stacks** in the same kind cluster, "Normal (baseline)" and "Skypiea
(predictive)", and the browser shows how they differ. This file is the design and reference;
the presenter script is in [`DEMO.md`](DEMO.md#live-console-7-minute-presenter-script).

## 1. Vocabulary: real profiles only (no SYNTHETIC profile)

The console uses only tool classes that a **real-data** predictor profile knows. The
vocabularies below are read from the predictor image's bundle manifests by
`python -m agent.vocab --image localhost:5001/skypiea/predictor:af000231bc7b`, which writes
[`configs/live/predictor_vocab.json`](../configs/live/predictor_vocab.json) and
[`configs/live/predictor_probe.json`](../configs/live/predictor_probe.json) (generated, not
typed). `tests/test_live_tasks.py` fails if a console tool leaves the vocabulary.

**Labels used on screen and in logs** (from the manifests, not from the profile names):

- support: *support profile, trained on tau2-bench traces* (bundle `lgbm-support-aabdfd2`,
  manifest `source: tau2_bench`).
- coding: *coding profile: model trained on TraceLab traces, warm policy tuned on TraceLab +
  Copilot*. The request asked for "trained on TraceLab + Copilot traces"; the manifest of
  `lgbm-coding-af00023` says `source: tracelab` (vocabulary: TraceLab's native classes), and
  `configs/model/profiles.yaml` tunes the coding **policy** on both sources' VAL splits. So the
  label says exactly that.

The `prd` profile stays in the repository and in the image; the console never selects it.

Output of `python -m agent.vocab`:

```
predictor image predictor:af000231bc7b
  coding: lgbm-coding-af00023 (source tracelab, synthetic False): agent, build, git, other, package, planning, read_file, run_tests, schedule, search, shell_io, shell_other, user_io, web, write_file
  support: lgbm-support-aabdfd2 (source tau2_bench, synthetic None): escalate, flight_search, other, reservation_book, reservation_cancel, reservation_lookup, reservation_modify, think, user_lookup
| task | profile | LLM tool | gateway class | in vocabulary | server |
|---|---|---|---|---|---|
| airline | support | user_lookup | user_lookup | yes | user_lookup |
| airline | support | reservation_lookup | reservation_lookup | yes | travel_booking |
| airline | support | flight_search | flight_search | yes | travel_booking |
| airline | support | reservation_modify | reservation_modify | yes | travel_booking |
| airline | support | reservation_cancel | reservation_cancel | yes | travel_booking |
| airline | support | transfer_to_human | escalate | yes | post_comment |
| codefix | coding | search | search | yes | search |
| codefix | coding | read_file | read_file | yes | read_file |
| codefix | coding | edit_file | write_file | yes | edit_file |
| codefix | coding | run_tests | run_tests | yes | run_tests |
| codefix | coding | shell | shell_other | yes | shell |

Predictor along the scripted flows (history with 3 s gaps and 200 ms calls; the live gaps differ):
| profile | after | next in flow | mode | top-3 | warms |
|---|---|---|---|---|---|
| support | (turn start, empty) | user_lookup | ranker | user_lookup 0.50, reservation_lookup 0.41, flight_search 0.02 | user_lookup |
| support | user_lookup | reservation_lookup | ranker | reservation_lookup 0.84, flight_search 0.04, reservation_book 0.03 | reservation_lookup |
| support | user_lookup > reservation_lookup | flight_search | ranker | escalate 0.45, flight_search 0.22, reservation_lookup 0.19 | escalate |
| support | user_lookup > reservation_lookup > flight_search | reservation_modify | ranker | flight_search 0.65, reservation_modify 0.10, reservation_cancel 0.06 | flight_search |
| support | user_lookup > reservation_lookup > flight_search > reservation_modify | - | prior | END 0.76, reservation_modify 0.14, reservation_cancel 0.02 | nothing (abstains) |
| coding | (turn start, empty) | search | ranker | search 0.38, read_file 0.29, shell_other 0.15 | git, read_file, search, shell_other |
| coding | search | read_file | ranker | search 0.54, read_file 0.35, shell_other 0.05 | read_file, search |
| coding | search > read_file | write_file | ranker | read_file 0.47, search 0.35, shell_other 0.06 | read_file, search |
| coding | search > read_file > write_file | run_tests | ranker | write_file 0.44, read_file 0.22, search 0.14 | read_file, search, write_file |
| coding | search > read_file > write_file > run_tests | shell_other | prior | shell_io 0.22, run_tests 0.20, write_file 0.20 | nothing (abstains) |
| coding | search > read_file > write_file > run_tests > shell_other | - | ranker | shell_other 0.30, read_file 0.17, shell_io 0.13 | read_file, run_tests, search, shell_io, shell_other |
```

What this means for the demo, said honestly on screen:

- **Support: prediction works on servers, not classes.** `tools/tool_map.yaml` routes all five
  airline classes to one server, `travel_booking`. Once `travel_booking` is warm, the next
  airline call finds it warm whatever the predictor said. The places where prediction can
  matter are: the turn start (user_lookup, p 0.50), user_lookup -> reservation_lookup (p 0.84:
  warms `travel_booking`), and a hand-over (`escalate` -> `post_comment`). After
  reservation_lookup the profile's top guess is `escalate` (0.45), which warms `post_comment`;
  that is what tau2-bench agents often did.
- **Coding:** the first-tool distribution is broad (search 0.38, read_file 0.29), so the
  turn-start hint warms several servers. The profile **abstains** after run_tests (mode
  `prior`: no decision), so `shell` after `run_tests` is never predicted.
- Where the flow diverges from the training traces (the LLM picks its own order), the console
  shows the predictor's top-3 and whether the next call matched.

Mock data: `user_lookup(with_reservations=true)` lists the user's reservation ids and
`travel_booking(detail=true)` returns owner, route, day and the new flight on modify. Both are
new optional arguments, off by default (existing calls answer exactly as before, tested). The
autopilot customer is `priya_menon_1119`, reservation `R35033531` (business, SK216 GOI->HYD,
day 45). The coding tools are deterministic mocks over the sample repo `minicalc`; an edit is
not visible to later calls, since each tool runs in its own pod.

## 2. Two stacks in one kind cluster

`demo/live_stacks.sh up [support|coding]` (called by `make live`) deploys the chart twice:

| | `skypiea-base`: "Normal (baseline)" | `skypiea-pred`: "Skypiea (predictive)" |
|---|---|---|
| tools | 16 Deployments at 0 | 16 Deployments at 0 |
| gateway | turn hint off (default) | `platform.gateway.turnHint=true` |
| controller | namespace-scoped RBAC, 2 starts at once | same |
| predictor | stub with stand-in policies: `scale_to_zero` (default) or `keep_alive` 30 s | real predictor image `af000231bc7b`, profile `support` or `coding` |
| monitoring | off | Prometheus + Grafana scraping **both** namespaces (`monitoring.extraNamespaces`, read-only Role in `skypiea-base`), live dashboard |

Both stacks use the same images (tools and platform at the current image tag) and the same
node. Each controller allows 2 concurrent starts (`maxScaleUpsPerNode: 2`), so up to 4 starts
can compete for the node. The console records start contention for every call: the largest
number of pods starting on the node (both stacks) while the call was in flight, sampled from
both controllers every 0.25 s.

Before the stacks start, `live_stacks.sh` pre-pulls the 16 tool images onto the worker node
(`crictl pull`). So every start is a **cached** start, like E0's "cached" condition, on both
stacks alike. Without the pre-pull, the first start of an image on a new cluster includes the
image pull. That penalises whichever stack starts it first; we saw this in the first test
conversation.

The predictor image holds the real bundles; a fresh clone cannot build them without the
datasets (README "Quickstart": `make data-all model-all predictor-image`). `make live` fails
with that message if the image is in neither the local registry nor Docker.

Resource use on the laptop (docs/DEMO.md has the table): both stacks idle take about
0.7 GiB (control plane) + 1.1 GiB (worker) of Docker memory.

## 3. Mirrored execution

Each tool call the LLM makes goes to **both** gateways with the same arguments and the same
MCP session id (`console/engine.py`). The conversation runs on the **predictive** stack, and
the LLM sees its result. The baseline repeats every call in its own lane **with the same
gap**: a baseline call is issued exactly as long after the baseline's previous call ended as
the predictive call was after the predictive stack's previous call ended. That gap is the LLM's
think time, and between turns the human's time. Both gaps are recorded per call
(`pred_gap_ms`, `base_gap_ms`) and shown in the timeline.

Why: our first design waited for both stacks before the LLM's next step. When the baseline
was cold and therefore slower, the predictive stack's warm-up got the baseline's extra time as
free lead time. In a verification run this turned a 2.5 s wait into 0.5 s. We discarded those
runs (see the report) and changed the design.

- **concurrent** (default): the baseline lane runs alongside the conversation. This is the
  most visual mode; the two stacks' pod starts can compete for the node (recorded). One limit
  cannot be removed: the next call only exists once the LLM has the predictive result. When a
  predictive call is **slower** than the baseline's (the predictive stack cold, the baseline
  warm), the baseline's next gap is longer by the difference. That can only leave the baseline
  colder, never warmer. The console tags such calls "gap +X vs Skypiea (waited for the LLM)"
  and records both gaps.
- **sequential**: the predictive stack runs the turn live. When the turn ends, the baseline
  repeats that turn's calls with the same gaps before the next turn may start. The stacks'
  calls never overlap. A turn's first baseline call can come later than its gap (recorded),
  which can only make the baseline colder under keep-alive. Under scale-to-zero it makes no
  difference once the pod is gone.

The conversation continues with the predictive stack's result. The mock tools are
deterministic, so both results should be identical; the console compares them and flags
`RESULTS DIFFER`. The mode is shown in the header and stored on every call.

Per call and per stack the console records: tool and gateway class, LLM reply id and position
in the reply, the LLM think time before the reply, wall latency, added latency, cold/warm,
held ms, predicted and warm source (`hint`/`call`), ok, and start contention. It also stores
the baseline policy, mirror mode and hint setting, and whether the model wrote the call as text
(`tool_call_from_text`, see below). These are metadata only: prompts, arguments and results
are never stored or exported. Replay keeps the arguments in memory only.

The agent rules that differ from the CLI agent (`agent/loop.py`, which is unchanged):

- A text reply ends the agent's turn (the human answers; no simulated user).
- `null` arguments are dropped, since small models send null for optional parameters.
- On a tool error the LLM sees the tool's own message, so it can fix its arguments.
- llama3.1 sometimes **writes** a tool call as JSON text. If the text holds exactly one
  `{"name": <known tool>, "parameters": {...}}`, the console executes it and marks it "the
  model wrote this call as text; the console recovered it". It is counted in the CSV.

## 4. Turn-start prewarm hint

When a user turn starts, and the console's **Hint** toggle is on (off by default), the agent
sends `POST /v1/hint {"turn": n}` with the session id to the **predictive** gateway only. The
gateway asks the predictor with the session's history (empty on turn 1: the first-tool
distribution learned from training), marked `trigger: "turn_start"`. It then applies the warm
decisions, so warming overlaps the LLM's think time and, with a human, the typing. The contract
extension, the flag and its tests are in `docs/predictor_contract.md` ("Turn-start hint"),
`tests/test_gateway_hint.py` and `tests/test_service_hint.py`. A call that a hint's decision
predicted is labelled **warmed by hint** in the console and `X-Skypiea-Warm-Source: hint` on
the wire.

## 5. The console

`python -m console` (`make live-console`) serves http://127.0.0.1:8090 with Starlette and
uvicorn, which are already in the lock. Updates arrive as server-sent events (`/events`). The
page is hand-written HTML, CSS, JS and inline SVG under `console/static/`, with no CDN, fonts
or external scripts (tested). It has a light and a dark theme, large type, and `?snapshot=1`
renders the current state once (for screenshots and printing).

| Panel | Source |
|---|---|
| Chat, with tool calls grouped by LLM reply | the console's own conversation. A reply with several calls says "prediction cannot help between calls sent in the same reply" |
| Per-stack timeline and totals | gateway response headers per call. Totals: time the agent waited on tools, cold calls, p50/p95 added latency (nearest rank, with n), idle pod-seconds (each controller's `skypiea_pod_seconds_idle_total`, delta since the conversation began), estimated idle cost = idle pod-s × the tool tier's CPU request × `vcpu_second_usd` from `configs/model/pricing.yaml` (`status: assumption`, shown) |
| Pod grid (16 tools per stack) | each controller's `/v1/state`, every 0.25 s: cold / starting / warm, ready/replicas, and "warm for" as measured by the console from the first poll that saw it ready |
| Predictor | the predictive gateway's `/v1/sessions/<sid>` (the data behind `skypiea trace` / `explain`): every decision (after a call, or a turn-start hint) with its top-3, what it warmed, and whether the next call matched (top-1 right? warmed?) |
| Think window per call | the LLM think time before the reply vs the cold start the **baseline** stack measured for the same call (its added latency when it was cold). If the baseline call was warm, it says "not measurable" |
| Predictor health | `/healthz` through the API server's service proxy (survives the pod being killed) |
| Footer | local kind on a laptop, LLM model, profile label and model version, cost assumption |

Buttons: **Reset to zero** waits until both controllers report every tool at 0 (it shows the
longest TTL left and never forces pods down), then starts a new conversation. The others are
**Baseline policy** (scale-to-zero or keep-alive 30 s), **Hint** on/off, **Mirror**
concurrent/sequential, **Kill / Restart predictor** (`kubectl scale` of `skypiea-model`;
the gateway falls back to scale-to-zero), **Replay last** (the last conversation's calls on
both stacks with the recorded gaps, no LLM) and **Export CSV** (also saved under
`.live/exports/`). The **Demo** selector switches the predictive stack's profile with helm,
which takes about 30-60 s.

`python -m demo.console_run` drives the console through the same HTTP API and saves
`events.jsonl`, `export.csv` and `summary.json`. The verification runs used it.

## 6. Grafana: "Skypiea Live: baseline vs predictive"

`make live` provisions it in the predictive stack's Grafana (http://127.0.0.1:3001, anonymous
viewer, 5 s refresh). It shows per-stack added latency p50/p95, cold rate, calls and cold
calls, idle pod-seconds, warm pool size, prewarm hits/misses, turn-start hints and the warms
they caused, and predictor round trip and errors, all split by `namespace`. The dashboard is
generated by `python -m demo.make_live_dashboard` and needs no plugin. Prometheus sees
1-minute windows; per-call values are in the console.

## 7. More lead time (branch feat/console-leadtime)

The first console runs showed that correct predictions usually arrived too late: the LLM
thinks for 0.6-0.9 s, and a start takes 2-3 s. The only clear wins came from pods that an
earlier decision had warmed. Four additions try to give the predictor more lead time. Each is
behind a switch, and every shipped default and result path is unchanged:

| Addition | Switch (default) | What it does |
|---|---|---|
| Typing-start hint | console **Hint** select: `off` / `at Enter` / `at typing start` (off) | `at typing start`: the browser sends one metadata-only `POST /api/typing {"event":"start"}` on the first keystroke of a turn (once per turn), and the console sends the turn hint at that moment. A hint for turn 1 is also sent when a conversation opens. `at Enter` is the earlier behaviour. If the box is emptied without sending, the turn counts as **abandoned**: the idle pod-seconds of the servers its hint warmed, over the 40 s after the hint, are shown and stored (an upper bound, since other idle pods of the same servers count too) |
| Two-step lookahead | console **Lookahead** button; chart `platform.predictor.decideOverrides` (empty = bundle policy, lookahead 1) | the predictor also warms the most likely tool **two batches ahead**, only above the threshold (`predictors/decide.py`, `lookahead`). It is applied after the bundle's golden check (`PREDICTOR_DECIDE_OVERRIDES`, lookahead only) and reported in `/healthz`. In the simulator it did **not** pay off: see `results/sim_lookahead_val.md` / `_test.md` |
| Console tool mapping | `LIVE_CONSOLE_MAP=1` in `make live` (chart `platform.classOverrides`, default empty) | `configs/live/console_tool_map.yaml`: flight_search runs on `search`, the handover (escalate) on `generic_fallback`, reservations stay on `travel_booking`. Both stacks get the same mapping. Shown in the header as "console mapping". `tools/tool_map.yaml` (AWS, every result) is unchanged |
| Early connect | chart `platform.gateway.earlyConnect` (false) | on the cold path the gateway polls the `/healthz` of pods that have an address but are not Ready yet, every 50 ms, and uses the first that answers. This saves the wait for the readiness probe (period 1 s) and the EndpointSlice update. Measured separately (report) |

Per turn the console now shows and exports (`Turns CSV`): the hint sources, the **lead time**
(first hint of the turn → Enter), the typing time, and whether the first call of the turn
found a warm pod on Skypiea (and the baseline). The autopilot "types" each message at an
assumed **200 characters per minute** (`autopilot_cpm`; `CONSOLE_AUTOPILOT_CPM`). This is a
typing model, not a measurement, and it applies in every hint mode, so the cells differ only
in when the hint fires.

**Known limit of the typing hint:** the support profile's warm decisions last 30 s (TTL), plus
the 5 s purge. A message that takes longer than about 35 s to type outlasts the pod the hint
started, and the first call is cold again. We saw this in the first test conversation: 43 s of
typing, user_lookup cold.

The predictor image for the console is built from this checkout's service code and the three
real bundles (`BUNDLES_DIR`, default `models/`; see `docs/PORTABLE_MODELS.md`). It is tagged
`console-<service commit>`. Without the bundles, `make live` falls back to the shipped image
`af000231bc7b`, and the lookahead toggle is unavailable.
