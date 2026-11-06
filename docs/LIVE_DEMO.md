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

| Part | Real or mock |
|---|---|
| Tool containers (16 MCP servers per stack), their cold starts, the gateways, the controllers | **Real**, on kind. Every tool call is sent through both gateways and waits for a real pod. |
| The predictor | **Real** trained bundles: support profile `lgbm-support-aabdfd2` (trained on **tau2-bench** traces) for Skye; coding profile `lgbm-coding-af00023` (ranker trained on **TraceLab** traces, warm policy tuned on TraceLab + **Copilot** VAL) for Dev assistant. It sees tool names and timing only. |
| The LLM | **Real**, local (Ollama): llama3.1:8b by default, qwen2.5:7b selectable. |
| Skypiea Air customers, bookings, flights, fares, offers, seats | **Mock** (fictional, `agent/skyair/`). Writes persist for the conversation; Reset restores the database. New customers and booking codes made in a conversation are fictional and live only in it. |
| The `sortkit` repository and its tests | **Mock** (`agent/devrepo/sortkit.json`). `run_tests` is a **simulated test runner**: it parses the files and evaluates literal asserts against its own reference behaviour; **no code is executed**, least of all code the model wrote. |
| Customer lines in Autopilot / Story mode | **Scripted** (prepared text); the model's replies, tool calls and all timings are live. The scripted customer "types" at 200 characters a minute (an assumption). |
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
  changed. Unchanged.
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

## Story mode

Each **Act** button resets, then sends prepared lines to the **live** model one by one (the
autopilot, with its typing model). Nothing is pre-recorded.

| Demo | Act | Lines (customer side) |
|---|---|---|
| Skypiea Air | 1: a new customer books a trip | new customer Nova Quill, SKA → SKE on 2026-12-07, Saver, confirm, add a bag |
| Skypiea Air | 2: change the schedule | Tara Patel (one active booking), later flight on the same route, confirm (truth check visible) |
| Skypiea Air | 3: kill the predictor mid-conversation | Dev Singh; the predictor is scaled to 0 after turn 1 (bag, offers) and restarted when the act ends |
| Code fix | Act: fix the failing merge_sort test | fix and run the tests, which files changed, then an out-of-repository question |

## Model bake-off

`python -m console.bakeoff --models llama3.1:8b,qwen2.5:7b --reps 2 --out results/live_bakeoff`
(files: one JSON report, calls CSV and turns CSV per conversation, `summary.csv`,
`summary.md`). The same five scripted personas for each model (new customer books a trip,
change schedule, cancel and refund, offers, failed verification), 2 conversations each,
hint off, 2026-11-06 18:00-18:40 UTC. **Demo runs, not results** (n = 10 conversations per
model; scripted customer; live model, tools and stacks).

| | llama3.1:8b | qwen2.5:7b |
|---|---|---|
| Task completed (judged from the database and write log) | **8 / 10** | 6 / 10 |
| by persona: new customer / change / cancel+refund / offers / failed verification | 2 / 2 / 2 / 0 / 2 | 0 / 2 / 2 / 0 / 2 |
| First-try valid tool calls (mean per conversation) | 0.88 | 0.87 |
| Booking codes invented by the model (1) | 4 | 2 |
| Prices quoted that no tool returned | 0 | 0 |
| Truth badges ("Not done: ...") | **0** | 4 (all true: claimed a booking twice and a promo twice that did not happen) |
| Tool calls written as text | 0 | 0 |
| `transfer_to_human` called | 10 / 10 (8 not needed) | 0 / 10 |
| LLM think time per reply: median of conversation medians / largest p95 | 0.95 s / 9.08 s | 0.81 s / 25.17 s (2) |

(1) The recorded `invented_codes` field also counted the customer's own wrong code ZZZZZZ in
the failed-verification persona (turn 3, one per conversation, both models); it is subtracted
here (recorded files unchanged; the report now separates `unknown_codes_from_customer`).
(2) The 25 s reply is qwen's first conversation right after the model switch, consistent with
the model loading (not verified).

**Default: llama3.1:8b** (unchanged), chosen by correctness: more tasks completed and no false
"done" claim. Its weak spot is an unneeded hand-over to a human at the end of most
conversations. Both models failed the offers persona: neither called `verify_customer` after
the customer gave the email, and the promo call was refused ("not verified"). Time saved by
Skypiea was not used to choose.

Story acts and the out-of-scope persona with each model: `results/live_stories/` (see
`docs/handoff/W1_REPORT.md`).

## Mapping to the predictor's vocabulary

Support profile (tau2-bench): find / verify / get customer → user_lookup; get booking →
reservation_lookup; search flights → flight_search; change flight and **add baggage →
reservation_modify**; cancel booking and **issue refund → reservation_cancel**; **book flight →
reservation_book**; transfer to a human → escalate. **Offers (list_offers, apply_promo) →
promo_code_lookup, not in the vocabulary**: no prediction, normal behaviour. A tool name the
model invents is sent as "other": no prediction, normal behaviour.

## Known limits of this demo

- The kind stacks run the pre-W1 gateway image: the live CSV's `*_held_ms` still uses the old
  definition (call arrival to resume, can exceed 150 ms) and `*_hold_late_ms` is empty. The
  FR-9 fix is in the code and tested (`docs/PRD_STATUS.md` FR-9).
- Small models make mistakes the tools refuse (wrong codes, missing verification); the truth
  check is rule-based and can miss claims.
- The mock database lives in the console process; a console restart resets every session.

## Presenter script

See [`DEMO.md`](DEMO.md#live-demo-5-minute-presenter-script) (5 minutes around story mode, and
a 1-minute version). Backup material for a talk without a network or cluster:
[`docs/live_backup/`](live_backup/) (`w1/` is the newest set).
