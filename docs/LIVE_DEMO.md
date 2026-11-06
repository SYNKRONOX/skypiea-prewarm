# Live demo: Skypiea Air with Skye

A presentation view of the Live Console. A visitor chats with **Skye**, the assistant of
**Skypiea Air**, a fictional airline. Every tool call Skye makes goes to two copies of the tool
platform on a local kind cluster: **Normal** (scale-to-zero, or keep-alive 30 s) and
**Skypiea** (a predictor starts the next tool's container while the LLM is still thinking).
The screen shows, live, how long Skye waited on tools on each side, and what the
booking database really holds.

> **The setup:** tool containers are real and cold-start for real; the airline data is a mock
> database inside the console; the predictor sees only tool names and timing.
> Numbers on the screen are **demo runs, not results** (one laptop, one small LLM; cold
> starts on kind are shorter than on AWS).

![Live demo, light theme](img/live_demo_light.png)

## Run it

```bash
make setup                       # once: .venv from requirements.lock
ollama pull llama3.1:8b          # once, where Ollama runs
make fetch-models                # predictor bundles from GitHub Release v1.2 (verified)
make live                        # both stacks + console: http://127.0.0.1:8090
make live-clean                  # stop
```

On a machine that already has the bundles in `models/`, run
`BUNDLES_DIR=models make live` instead. The archive to publish is built by
`make models-archive` (see `docs/PORTABLE_MODELS.md`).

The console opens on Skypiea Air with an empty conversation. Override the LLM with
`AGENT_BASE_URL` / `AGENT_MODEL`.

## What is on the screen

| Area | What it shows | Where the data comes from |
|---|---|---|
| Header | Skypiea Air wordmark (original, fictional), demo, Normal policy (scale-to-zero / keep-alive 30 s), hint (off / at Enter / on typing), Kill / Restart predictor, health dot (cluster, predictor, LLM), Details, theme, "?" | console settings; controller and predictor health |
| Left | Chat with Skye (tool calls as small pills, typing indicator), **Booking record**, input, Send, Reset, Autopilot, Personas | the conversation; the session's mock database |
| Right top | One sentence: how long you waited on tools with Normal and with Skypiea, the difference (can be negative), extra warm pod-seconds and their estimated cost, n calls | the console's totals (gateway headers, controller idle time, `pricing.yaml`) |
| Right middle | One row per tool call on each side, in plain words, with a bar, the time and warm / cold. **Click a row** for one sentence: predicted and started early, predicted but too late (with numbers), not predicted, batch, or unknown tool | gateway response headers |
| Under Skypiea | "Next likely: Get booking 84%, Search flights 4% → warming Get booking"; a spinner while a Skypiea pod is starting | predictor decisions; controller pod state |
| Details drawer | totals, pod grids, predictor top-3 per call, turn hints and lead times, abandoned turns, think window per call, lookahead / mirror / replay, vocabulary mapping, truth-check rules, CSV exports, Grafana | as above |

### Truth panel

Small LLMs sometimes say they did something they did not do. The **Booking record** card
shows the verified customer's bookings straight from the database, plus a log of every real
write (change, cancel, refund, baggage) and whether it was done, only quoted (Skye asked for
confirmation) or refused.

After each Skye reply, simple rules look for "done" claims (changed, cancelled, refunded,
baggage added) and compare them with the writes that succeeded in that turn. If they disagree,
the reply gets a red badge such as **"Not done: the booking was not changed"**. Skye's text is
never edited. The verdict is in the turns CSV (`truth_claims`, `truth_verdict`, `truth_badge`,
`truth_note_sent`, `truth_corrected`). When a badge fires, the console adds one system note
for the model ("say plainly it was NOT done"), and logs whether the next reply corrected
itself (a keyword heuristic). The rules are regular expressions (`console/truth.py`): they can
miss unusual phrasings.

![Live demo, dark theme](img/live_demo_dark.png)

## Mapping to the predictor's vocabulary

The predictor was trained on the airline support vocabulary (tau2-bench). Skye's tools map
onto it: find / verify / get customer → user_lookup; get booking → reservation_lookup;
search flights → flight_search; change flight and **add baggage → reservation_modify**;
cancel booking and **issue refund → reservation_cancel**; transfer to a human → escalate.
A tool name the model invents is sent as "other": no prediction, normal behaviour.

## Presenter script

See [`DEMO.md`](DEMO.md#live-demo-5-minute-presenter-script) (5 minutes, and a 1-minute
version). Backup material for a talk without a network or cluster:
[`docs/live_backup/`](live_backup/).
