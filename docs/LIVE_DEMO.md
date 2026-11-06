# Live demo: Skypiea Air with Skye (and a code fix with Dev assistant)

A presentation view of the Live Console. A visitor chats with **Skye**, the assistant of
**Skypiea Air**, a fictional airline (or, in the second demo, with **Dev assistant**, who fixes
a bug in a small mock repository). Every tool call goes to two copies of the tool platform on a
local kind cluster: **Normal** (scale-to-zero, or keep-alive 30 s) and **Skypiea** (a predictor
starts the next tool's container while the LLM is still thinking). The screen shows, live, how
long the agent waited on tools on each side, and what the booking database (or the repository)
really holds.

> **The setup:** tool containers are real and cold-start for real; the airline data and the
> repository are mocks inside the console; the predictor sees only tool names and timing.
> Numbers on the screen are **demo runs, not results** (one laptop, one small LLM; cold
> starts on kind are shorter than on AWS).

![Live demo, light theme](img/live_demo_light.png)

## Run it

```bash
make setup                       # once: .venv from requirements.lock
ollama pull llama3.1:8b          # once, where Ollama runs (the default model)
ollama pull qwen2.5:7b           # optional: second model for the Model dropdown
make fetch-models                # predictor bundles from GitHub Release v1.2 (verified)
make live                        # both stacks + console: http://127.0.0.1:8090
make live-clean                  # stop
```

**Until the v1.2 release exists**, `make fetch-models` stops with exit code 2 and says so. On
a machine that already has the bundles in `models/`, run `BUNDLES_DIR=models make live`
instead. The archive to publish is built by `make models-archive` (`docs/PORTABLE_MODELS.md`).

The console opens on Skypiea Air with an empty conversation and llama3.1:8b (override with
`AGENT_BASE_URL` / `AGENT_MODEL`).

## What is mock and what is real

Every number and element, typed as measured / estimated / trained offline / mocked /
scripted / live, with a 2-minute self-check and the honest answers to the usual questions:
[`MEASURED_VS_SCRIPTED.md`](MEASURED_VS_SCRIPTED.md).

| Part | Real or mock |
|---|---|
| Tool containers (16 MCP servers per stack), their cold starts, the gateways, the controllers | **Real**, on kind. Every tool call is sent through both gateways and waits for a real pod. |
| The predictor | **Real** trained bundles: support profile `lgbm-support-aabdfd2` (trained on **tau2-bench** traces) for Skye; coding profile `lgbm-coding-af00023` (ranker trained on **TraceLab** traces, warm policy tuned on TraceLab + **Copilot** VAL) for Dev assistant. It sees tool names and timing only. |
| The LLM | **Real**, local (Ollama): llama3.1:8b by default, qwen2.5:7b selectable. |
| Skypiea Air customers, bookings, flights, fares, offers, seats | **Mock** (fictional, `agent/skyair/`). Writes persist for the conversation; Reset restores the database. New customers and booking codes made in a conversation are fictional and live only in it. |
| The `sortkit` repository and its tests | **Mock** (`agent/devrepo/sortkit.json`). `run_tests` is a **simulated test runner**: it parses the files and evaluates literal asserts against its own reference behaviour; **no code is executed**, least of all code the model wrote. |
| Customer lines in Auto-play / Scenarios ("Scripted customer · turn N"; typed lines say "You") | **Scripted** (prepared text); the model's replies, tool calls and all timings are live. The scripted customer "types" at 200 characters a minute (an assumption). |
| What the tool pods receive | Timing calls only: the tool class and no customer or repository data. The answer the model sees comes from the mock in the console. |

## What is on the screen

| Area | What it shows | Where the data comes from |
|---|---|---|
| Header | wordmark (original, fictional), demo, **Model** (local Ollama models that support tool calling; a hosted OpenAI-compatible model if `AGENT_BASE_URL`/`AGENT_API_KEY` are set), Normal policy, hint (off / at Enter / on typing), Kill / Restart predictor, health dot, story badge, Details, theme, "?" | console settings; Ollama `/api/tags` + `/api/show`; controller and predictor health |
| Left | Chat (tool calls as pills), **Story: Act 1 / 2 / 3** (code fix: **Act**), Personas, the **booking card** (code fix: **Repository card**), input, Send, Reset, Autopilot | the conversation; the session's mock |
| Right top | One sentence: tool wait with Normal and with Skypiea, the difference (can be negative), idle pod-seconds of both stacks and the estimated extra cost, n calls | the console's totals (gateway headers; controller idle counters read every 2 s; `pricing.yaml`) |
| Right middle | One row per tool call per side, plain words, a bar, time, warm / cold. **Click a row** for one sentence (see "Why was it warm?") | gateway response headers and the gateway's session record |
| Under Skypiea | "Next likely: ... → warming ..."; a spinner while a Skypiea pod starts | predictor decisions; controller pod state |
| Footer | cluster, the **model in use**, predictor profile and version, "Demo runs, not results" | |
| Details drawer | totals, pod grids, predictor top-3 per call, hint lead times, abandoned turns, think window, lookahead / mirror / replay, vocabulary, truth rules, CSV exports (every row names the model), Grafana | as above |

Switching the model resets the conversation (drain to 0 pods, new conversation), so one
conversation never mixes two models.

### Skye's behaviour (Skypiea Air)

- **Verification first** (email or booking code) before anything about a customer is shown or
  changed. `find_customer` also accepts the email or code when the customer gives it with
  their name, and then verifies by the same rule (both small models never called
  `verify_customer` in that case).
- **One active booking:** if the verified customer has exactly one confirmed booking, booking
  tools use it when the model leaves the code out; with several, the tool returns the
  customer's codes and Skye must ask which one. A wrong code returns the customer's valid
  codes (never before verification).
- **Customer sidebar:** after verification, a new booking or any change, Skye's system prompt
  gets the customer's facts the way a CRM shows them to a support agent: every booking with
  code, status, fare class, change fee, refundability, passengers, bags, paid, flights and
  seats, and the bag price. Prices and fees may be quoted only from tool results or this
  sidebar.
- **New customers:** a name that is not found → Skye welcomes a new customer, asks origin,
  destination and date, searches flights (with Saver / Flex / Business prices), quotes,
  asks for confirmation and books (`book_flight`, class `reservation_book`). The new
  customer is verified by that booking and can then check it, change the flight, add bags,
  cancel or apply an offer. A second identical booking is refused ("duplicate").
- **Offers:** `list_offers` and `apply_promo` (fictional codes SKYWINTER10, BAG35, BIZ25).
  The support vocabulary has no promo tool, so these calls (class `promo_code_lookup`) are
  labelled **"unknown tool for the predictor: no prediction, normal behaviour"**.
- **Out of scope:** Skye says what she can help with and does not answer weather, visas,
  coding or general-knowledge questions.

### Booking card and truth check

The booking card is a small **boarding pass** per booking (route, flight, date, departure,
arrival, seat, fare, passengers, bags, paid, promo, status), with the changes made in this
conversation as **old → new** lines (flight, bags, status, paid) and a **new customer** badge.
Below it, a log of every real write (change, cancel, refund, baggage, booking, promo) and
whether it was done, only quoted (Skye asked for confirmation) or refused.

After each Skye reply, simple rules check two things, and Skye's text is never edited:

1. **"Done" claims** (changed, cancelled, refunded, baggage added; since W1 also booked and
   promo applied) against the writes that succeeded in that turn → red badge such as
   **"Not done: the booking was not changed"**.
2. **Prices**: every money amount in the reply ("$50", "$1,014", "50 USD") must equal a number
   that a tool result or the sidebar contained in this conversation → otherwise **"Not from
   a tool: $X"**. A total Skye adds up herself is flagged too (no tool returned it).

The verdicts are in the turns CSV (`truth_claims`, `truth_verdict`, `truth_badge`,
`truth_note_sent`, `truth_corrected`, `truth_price_verdict`, `truth_prices_unsourced`). When a
badge fires the console adds one system note for the model and logs whether the next reply
corrected itself (keyword heuristic). The rules are regular expressions (`console/truth.py`):
they can miss unusual phrasings.

![Live demo, dark theme](img/live_demo_dark.png)

### Why was a call warm? (never an invented prediction)

A Skypiea row says "started early because it was predicted ..." only when the gateway marked
the call predicted. A warm call that the latest prediction did not include names its real
cause, from the gateway's session record (CSV `pred_warm_reason`): **kept after use** (the pod
was still up after an earlier call; warm time counts from the last use), **earlier
decision** (an earlier prediction or turn-start hint warmed it, e.g. just before the predictor
was killed), or **already up** (left over from before this conversation). This explains the
Phase B predictor-kill run, where three calls were warm while the predictor was down: the
typing-mode hint sent at Reset had warmed the customer-lookup pod 0.9 s before the kill took
effect, and that pod then stayed warm after each use. A **Kill predictor** pressed before the
first message now drains and starts a new conversation, so a "no predictor" run starts cold.

### Summary sentence

The idle pod-seconds in the summary come from the controllers' counters, read every 2 s and
re-published every 2 s (before W1 they were only updated when a call ended, so the sentence
could say "kept pods warm 0.0 s longer" while Skypiea pods were warm). Until anything is
counted it says "No idle pod-seconds measured yet".

## Code fix: Dev assistant

**Demo → Code fix** switches the predictive stack to the coding profile (helm, 30-60 s). Dev
assistant works on `sortkit` (`sorting.py`: `merge_sort` with one planted bug, `_merge` drops
the right-hand tail; `quick_sort`; `search.py`: `binary_search`; tests). Tools: search, read
file, edit file, run tests (classes search, read_file, write_file, run_tests, all in the
coding vocabulary). The **Repository card** shows files changed and the test runs
("3 passed, 2 failed → 5 passed, 0 failed"), labelled *simulated test runner*. The persona card
lists suggested tasks ("Fix the failing merge_sort test", "Add binary search tests", ...).
Questions outside the repository get "That is not in this repository".

The simulated runner: `ast.parse` only; for each `test_*` it evaluates asserts of the form
`assert merge_sort([...]) == [...]` (also `binary_search`, `quick_sort`; `==`, `!=`, `is`) with
`ast.literal_eval` and its own reference functions. `merge_sort` behaves like the buggy code
until `_merge` keeps `right[j:]` (or loops over `right`) after its main loop. Any other
assert is "not evaluated" (never a pass); a test with no assert fails; a syntax error is a
collection error.

## Scenarios (Auto-play)

Each **Scenario** button resets, then sends prepared lines to the **live** model one by one
(Auto-play, with its typing model). Nothing is pre-recorded. In the chat, these lines are
labelled "Scripted customer · turn N"; lines you type are labelled "You". (In the code and
API they are still "story acts": `POST /api/story {"act": n}`, files `story_*`.)

| Demo | Scenario | Lines (customer side) |
|---|---|---|
| Skypiea Air | New customer books a trip | new customer Nova Quill, SKA → SKE on 2026-12-07, Saver, confirm, add a bag |
| Skypiea Air | Change a booking | Tara Patel (two active bookings; the customer names 5TGTBP), later flight on the same route, confirm (truth check visible) |
| Skypiea Air | Cancel and refund | Asha Reddy cancels S4Z64E (a refundable fare) and asks for the refund; the predictor is scaled to 0 after turn 1 and restarted when the scenario ends, so the later calls run without prediction |
| Code fix | Fix the failing merge_sort test | fix and run the tests, which files changed, then an out-of-repository question |

Recorded runs before the W1-night rename (`results/live_stories/`) used Act 3 = Dev Singh, bag
and offers; they are kept as they were.

## Watch the pods (`make watch-pods`)

```bash
make -f Makefile -f mk/console.mk watch-pods     # until mk/console.mk is included by the Makefile
```

Prints every pod event of `skypiea-base` (Normal) and `skypiea-pred` (Skypiea) with a UTC
timestamp, until Ctrl-C (read-only; `LIVE_KIND_CTX` overrides the kubectl context).

**What you should see:** at the start only the platform pods (gateway, controller, predictor,
model, Prometheus, Grafana). When Skye calls a tool, a tool pod (e.g. `travel-booking-...`) is
ADDED in both namespaces. On Normal it appears only after the call arrived, and the call waits
for it to become READY true. On Skypiea it often appears a second or two earlier, while the
LLM is still thinking, because the predictor asked for it; then the call finds it ready. After
about 30 s without use (TTL + 5 s) the pods are DELETED again, Skypiea's usually later than
Normal's (the idle cost). With the predictor killed, Skypiea's pods appear only on demand,
like Normal's.

## Model bake-off

`python -m console.bakeoff --models llama3.1:8b,qwen2.5:7b --reps 2 --out <dir>` (one JSON
report, calls CSV and turns CSV per conversation, `summary.csv`, `summary.md`). The same five
scripted personas for each model (new customer books a trip, change schedule, cancel and
refund, offers, failed verification), 2 conversations each, hint off. **Demo runs, not
results** (n = 10 conversations per model per version; scripted customer; live model, tools
and stacks). It was run three times on 2026-11-06 because the bake-off itself exposed
problems that were then fixed; every version is kept as recorded:

| Version | Directory | Skye / tools at that time |
|---|---|---|
| v1 (18:00-18:40 UTC) | `results/live_bakeoff/` | W1 tools; verification only through verify_customer |
| v2 (21:10-21:35 UTC) | `results/live_bakeoff_v2/` | + prompt nudge "verify right away" (W1-18) |
| v3 (21:45-22:30 UTC) | `results/live_bakeoff_v3/` | + find_customer verifies with a given email/code (W1-20); **current** |

| | llama3.1:8b v1 / v2 / v3 | qwen2.5:7b v1 / v2 / v3 |
|---|---|---|
| Task completed (judged from the database and write log) | 8 / 6 / **9** of 10 | 6 / 8 / **8** of 10 |
| v3 by persona: new customer, change, cancel+refund, offers, failed verification | 2, 1, 2, 2, 2 | 0, 2, 2, 2, 2 |
| First-try valid tool calls (mean per conversation) | 0.88 / 0.84 / 0.93 | 0.87 / 0.93 / 0.93 |
| Booking codes invented by the model (1) | 4 / 5 / 4 | 2 / 0 / 0 |
| False "done" claims (true "Not done" badges) (2) | 0 / 1 / 1 | 4 / 2 / 2 |
| Prices quoted that no tool returned ("Not from a tool") | 0 / 0 / 3 | 0 / 0 / 4 |
| `transfer_to_human` without being asked (3) | 8 / 8 / 8 | 0 / 0 / 0 |
| Tool calls written as text | 0 / 0 / 0 | 0 / 0 / 0 |
| LLM think time per reply, v3: median of conversation medians / largest p95 (4) | 0.96 s / 51.4 s | 0.85 s / 7.8 s |

(1) Codes the database never had and the customer never typed. In v1 the recorded field
also counted the customer's own wrong code ZZZZZZ (failed verification, one per
conversation); it is subtracted here. From v2 on the report separates
`unknown_codes_from_customer`. (2) All "Not done" badges were checked against the write
logs. Three qwen badges "Not done: the booking was not changed" (offers, v2 r2 and v3 r1, r2)
were a rule false positive ("your booking has been updated with the promo code"), fixed
afterwards (W1-21); they are not counted here. (3) Of 10 hand-overs per version; the 2 in
the failed-verification persona are allowed by the rules and not counted. (4) The large p95s
are the first conversation right after a model switch (llama v3 new_customer r1, qwen v1
new_customer r1, 25.2 s), consistent with the model loading (not verified).

**Default: llama3.1:8b** (unchanged), chosen by correctness, not by time saved: over the
three versions it completed 23 of 30 tasks (qwen 22) and made 2 false "done" claims (qwen 8:
it repeatedly told a new customer the trip was booked when only a quote existed). Its weak
spots: it invents booking codes (the tools refuse them) and hands over to a human at the end
of most conversations; qwen does neither. The difference in completed tasks is within
run-to-run variance (v1 and v2 disagreed). qwen2.5:7b is one click away in the Model dropdown.

Story acts and the out-of-scope persona with each model: `results/live_stories/` (earlier
runs in `v1/`, `v2/`, `superseded/`, each with a README); see `docs/handoff/W1_REPORT.md`.

## Mapping to the predictor's vocabulary

Support profile (tau2-bench): find / verify / get customer → user_lookup; get booking →
reservation_lookup; search flights → flight_search; change flight and **add baggage →
reservation_modify**; cancel booking and **issue refund → reservation_cancel**; **book flight →
reservation_book**; transfer to a human → escalate. **Offers (list_offers, apply_promo) →
promo_code_lookup, not in the vocabulary**: no prediction, normal behaviour. A tool name the
model invents is sent as "other": no prediction, normal behaviour.

## Known limits of this demo

- The W1 gateway (FR-9 `held_ms` <= 150 ms plus `hold_late_ms`) runs on the kind stacks since
  the fresh-clone `make live` of 2026-11-06 ~22:50 UTC. The bake-off and story runs recorded
  before that used the previous gateway image: their `*_held_ms` is the old definition (call
  arrival to resume, can exceed 150 ms) and `*_hold_late_ms` is empty.
- Small models make mistakes the tools refuse (wrong codes, missing verification); the truth
  check is rule-based and can miss claims.
- The mock database lives in the console process; a console restart resets every session.

## Presenter script

See [`DEMO.md`](DEMO.md#live-demo-5-minute-presenter-script) (5 minutes around story mode, and
a 1-minute version). Backup material for a talk without a network or cluster:
[`docs/live_backup/`](live_backup/) (`w1/` is the newest set).
