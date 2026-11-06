# What on the Live Console is measured, and what is scripted

One page for anyone who asks "is this real?". Every number and element of the console
(`make live`, http://127.0.0.1:8090) is typed below. Words used:

- **Measured**: read from a clock or a counter while the demo runs (gateway, controller, console).
- **Estimated**: computed from a measurement with an assumed constant (shown as such).
- **Trained offline**: comes from a model trained before the demo on recorded traces; it does not learn during the demo.
- **Mocked**: invented, fictional data or behaviour inside the console (no real airline, no real customer, no real repository).
- **Scripted**: text prepared in advance (the customer side in Auto-play and the Scenarios).
- **Live**: produced on the spot by a running component (the local LLM, the predictor, the pods).

## Every element

| Element on the console | Type | Where it comes from |
|---|---|---|
| Skye's replies, which tools she calls and with which arguments | Live | local LLM through Ollama (`console/llm_stream.py`) |
| Customer lines labelled "Scripted customer · turn N" (Auto-play, Scenario buttons) | Scripted | `agent/tasks/skye.py` `SCENARIOS`, `agent/tasks/stories.py` |
| Lines labelled "You · turn N" | Live | typed by the presenter |
| Tool pods starting, warm, stopping (pods panel, `make watch-pods`) | Live, measured | the controllers' `/v1/state` (`console/poller.py`), every 0.25 s (pods) and 2 s (idle counters) |
| Per-call wall time, added time, held time (Normal and Skypiea columns) | Measured | gateway response headers (`X-Skypiea-Added-Ms`, ...) and the console's own clock around each call |
| Cold / warm chip per call | Measured | gateway: no Ready pod when the call arrived (`X-Skypiea-Cold`) |
| "predicted", "warmed by the hint", "kept after use", "earlier decision" | Measured | gateway session record (`/sessions/<id>`), `console/engine.py:warm_reason` |
| Predictor top-3 and probabilities, "warmed" list | Live from a model **trained offline** | real predictor service, `support` profile bundle trained on tau2-bench traces; it does not learn during the demo |
| "unknown tool for the predictor: no prediction" | Live | the tool's class is not in the trained vocabulary (`configs/live/predictor_vocab.json`) |
| Waited on tools (s), cold calls, p50/p95 added | Measured | sums over the call records above |
| Idle pod-seconds | Measured | controller counter `skypiea_pod_seconds_idle_total`, read every 2 s |
| Idle cost in $ | Estimated | idle pod-seconds × CPU request × an assumed price (`configs/model/pricing.yaml`, status "assumption") |
| LLM think time per reply | Measured | console clock, request to complete reply |
| Typing time of the scripted customer | Estimated (an assumption) | 200 characters per minute (`CONSOLE_AUTOPILOT_CPM`), the same in every hint mode |
| "node: n starting" contention tags | Measured | pods starting on the node during the call (controllers' state) |
| Skypiea Air flights, customers, bookings, prices, offers, booking codes, seats | Mocked | fictional database generated with a fixed seed (`agent/skyair/`) |
| Booking record (boarding pass, write log incl. refused calls) | Mocked data, live writes | the conversation's own copy of the mock database; every write attempt is logged with its outcome |
| Truth badges ("Not done ...", "Not from a tool: $X") | Live, rule-based | regular expressions over Skye's reply vs the writes that succeeded (`console/truth.py`); no LLM judge |
| Tool pods' work (what a tool computes) | Mocked work, real pods | each pod is a real container that cold-starts for real and spends a configured service time of real CPU or file work (`tools/common/server.py`, `burn()`); Skye's answer comes from the in-console mock database. Pods receive no customer data |
| Code-fix demo repository and test results | Mocked | `agent/devrepo/sortkit.json`, simulated test runner (nothing is executed) |
| Footer "demo run, not a result" | – | every console number is one conversation on one laptop |

## Measured vs hardcoded, in four sentences

Whether a call is cold is decided by the gateway when the call arrives, in
`gateway/app.py:Gateway.handle_call`: if the tool's server has no Ready pod
(`self.tracker.ready(route.server) > 0` is false) it holds the call up to 150 ms and otherwise
marks it cold and waits for a pod. The added time is measured in the same function as arrival
to reply minus the upstream time (`added_ms = max(0.0, t_end - t_arrive - upstream_s) * 1000.0`),
and the console only copies it from the response headers. Which pods are started early is
decided by the predictor in `predictors/decide.py:Decider.decide` (probability above a
threshold `tau`, confidence above `min_conf`, timing and copies), and the controller
(`controller/core.py:WarmPool.plan`) starts and stops the pods. No number on the console is
typed in by hand; the scripted parts are the customer lines and the mock database.

## A 2-minute self-check

1. **Kill the predictor** (Details drawer, "Kill predictor"), then run a scenario. Every call
   still succeeds; the Skypiea column goes cold like the Normal one; no call is labelled
   "predicted". Restart it afterwards.
2. **Toggle Hint** (Details drawer). The header badge changes, every call row and turn row
   records the mode, and a change during a conversation is shown as a warning pill with who
   changed it.
3. **Run the same scenario twice.** The model's wording and the timings differ (live LLM,
   live pods); the mock database starts from the same state each time.
4. **Switch the model** (model dropdown). The conversation resets; every exported row names
   the model.
5. **`make watch-pods`** in a terminal: the tool pods of both stacks appear and disappear with
   timestamps while you chat; Skypiea's appear before the call, Normal's after it.

## Where Skypiea does NOT win

- **Tools the predictor never saw.** Offers (`list_offers`, `apply_promo`) map to a class
  that is not in the trained vocabulary: no prediction, normal behaviour, as cold as Normal.
- **Low-confidence steps.** When the top probability is below `min_conf` (0.3) or the last
  tool is unknown, the predictor abstains (`Decider.decide`), so the next call can be cold.
- **Idle cost.** Skypiea kept pods idle 4-11 times longer than the baseline in every live cell
  (README, lead-time table; `results/live_console_leadtime/`) and 5-11 times in the W1 story
  runs (`docs/handoff/W1_REPORT.md`). That is the price of warming early.
- **Against always-warm and keep-alive.** Always-warm is never cold in Scenario A (0 of 116
  calls) and almost never in B (3 of 319), against 18 of 116 and 18 of 319 for predictive, at
  6,534 and 14,675 idle pod-s vs 633 and 2,807 (`results/scenario_aws_summary.csv`). Against
  fixed keep-alive, predictive is on the same cost-latency curve: the cold-rate CIs overlap
  (README "AWS scenario runs", 20 sessions per policy).
- **Very short think times.** If the LLM answers before a pod can start (about 1.6-2.9 s on
  kind), even a correct prediction leaves the call cold: it is marked cold and "predicted"
  at the same time.

## The refused calls in the Booking record

The write log lists every write Skye attempted (book, change, cancel, bags, refund, promo) with
its outcome: **done**, **quote** (the tool asked for the customer's confirmation, nothing
written) or **refused**. A refusal is the mock booking system saying no, with its reason: not
verified yet, a booking code that does not exist or is not the customer's, a duplicate
booking, a promo whose conditions are not met, a refund on a non-refundable fare. They are
kept visible on purpose: they show the tools enforce the rules even when the small model
tries something wrong.

## Questions you can expect, with honest answers

1. **Did you hardcode this?** No number. Cold/warm, wait times and idle seconds are measured
   by the gateway and controllers on every call. The customer side of Auto-play is scripted,
   and the airline is a mock database.
2. **What is mock?** The airline, its customers, flights, prices and bookings; what a tool pod
   computes; the code-fix repository and its test runner.
3. **Is the customer real?** No. In Auto-play and the Scenarios the customer lines are
   prepared text ("Scripted customer"). You can type your own ("You").
4. **Is the LLM real?** Yes, a local model through Ollama (llama3.1:8b by default). It can make
   mistakes; the tools refuse wrong actions and the truth badges flag false claims.
5. **Does it learn?** No. The predictor was trained offline on recorded traces and is fixed
   during the demo. It only reads tool names and timing, never the conversation text.
6. **Why is it sometimes cold?** The predictor abstained (unknown tool, low confidence), the
   guess was wrong, the LLM answered faster than a pod starts, or the warm pod expired.
7. **Why does Skypiea cost more idle time?** Pods started early wait idle until the call comes
   (or never come); that is the trade-off, shown as idle pod-seconds and an estimated cost.
8. **Are these results?** No. Each console run is one conversation on one laptop: a demo run.
   The results are the AWS and simulator tables in the README.
