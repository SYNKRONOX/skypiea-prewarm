# Phase A handoff (branch feat/console-support, from main 9d62db2)

Autonomous run, 2026-11-06, 11:41-12:10 UTC. Nothing was pushed, merged, tagged or published.
No AWS was used, and no package was added. Decisions: `docs/handoff/DECISIONS.md` (#1-#9).
`make test`: **422 passed**; ruff clean; `make check-sizes` OK.

## Done (commits)

| Step | Commit(s) | What |
|---|---|---|
| 1 Skypiea Air database | 817b8cd | `agent/skyair/generate.py` (seed 2026) -> `agent/skyair/skyair_db.json` (110 KB): 50 customers (4 names appear twice, for "ambiguous -> ask again"), 150 bookings (codes, flights, passengers, fare Saver/Flex/Business, bags, paid), 336 flights over 14 days between six fictional airports (SKA-SKF), 13 cancelled by the airline. Fictional names only |
| 2 Tools | 817b8cd, b9df1d6, 5ed6a45 | `agent/skyair/tools.py` `Session`: find_customer, verify_customer (email or booking code), get_customer_details, get_booking, search_flights, change_flight, cancel_booking, add_baggage, issue_refund (only if eligible: Flex/Business fare or a flight cancelled by the airline, and the booking cancelled), transfer_to_human. Ambiguous name -> ask again; failed verification -> polite refusal; every customer/booking tool needs the customer verified; changes need `customer_confirmed=true` (otherwise a quote); writes persist in the session; a new conversation (Reset) restores the database. Tool classes map to the real `support` vocabulary (DECISIONS #2); the console mapping puts them on four distinct servers (user_lookup, travel_booking, search, generic_fallback) |
| 3 Skye | 0f95782, b24cf5e, a9601f8 | `agent/tasks/skye.py`: system prompt (greet, ask for the name, look up, verify, never invent, confirm before changes, summarise), ten tools, argument synonyms (DECISIONS #9). The gateways get the call class with EMPTY arguments (timing only; no customer data leaves the console); Skye's answers come from the session database (DECISIONS #1). An unknown tool is still routed (class `other`) and labelled "unknown tool: no prediction, normal behaviour". The console opens on Skypiea Air by default |
| 4 Personas | 817b8cd, 0f95782 | `GET /api/personas`: 6 personas with a unique name, email, booking code, fare and two suggested requests (a later same-route flight exists for each), plus one AMBIGUOUS name |
| 5 Fixes | d6b1f48 | supervised port-forwards (restart loop per forward; tested live: the predictive gateway pod was deleted and :18280 was back about 2 s after the rollout); gateway link health in the console status line; the predictor's health goes through the API server proxy (as before); every UI action is handled, and an unknown one returns the list of valid actions; money is never in scientific notation ("$0.00", "<$0.00001", "$0.00003", "$1.23"); seconds keep 2 decimals |
| 6 Typing hint | 0f95782 | "at typing start" now also re-sends the metadata-only hint at Enter. The lead-time results stay as recorded |
| 7 Portable models | c8a64e3 | `make fetch-models` downloads `https://github.com/SYNKRONOX/skypiea-prewarm/releases/download/v1.2/skypiea-models-af000231.tar.gz` + `SHA256SUMS` (owner/repo from `git remote origin`), verifies the archive and each file, and says clearly that the release does not exist yet (exit 2; checked: 404 today). `make models-archive` builds `dist/` (git-ignored). `docs/MODELS_ATTRIBUTION.md` (included in the archive as ATTRIBUTION.md); `docs/PORTABLE_MODELS.md` updated |
| 8 Tests | 83e78bd + later | `tests/test_skyair.py` (database determinism and size, lookup, ambiguity, verification incl. made-up ids, confirmation, persistence and reset, refund rules, unknown tools, argument synonyms, Skye in the console engine with a scripted LLM: no customer data to the pods), `tests/test_fetch_models.py` (local fake release: not published, http refused, checksum mismatch, tampered file, real round trip), console tests updated |

**dist/ archive (NOT published):** `dist/skypiea-models-af000231.tar.gz`, 1,003,524 bytes,
sha256 `918b36c5409ffaf261e37326cc290125ab99a69b56569c215dff7687989bdf23` (reproducible;
`dist/SHA256SUMS` holds the same line). To publish it, create GitHub Release v1.2 and attach
both files. That is your decision.

## Live smoke conversations (Ollama llama3.1:8b; functional checks, not measurements)

Five autopilot conversations with Skye (Tara Patel, hint at typing start), run between the
fixes above. Local event logs are in `.live/runs/skye_smoke*` (git-ignored; they contain
conversation text).

| Run | After commit | What happened |
|---|---|---|
| 1 | 0f95782 | verification FAILED although the email and code were right (the model passed a made-up customer_id) -> fixed in b9df1d6 |
| 2 | b9df1d6 | verified; change_flight failed (wrong old flight); Skye then **falsely claimed** "I've updated your flight to SKA123" -> errors now say "NOT made" and list alternatives (5ed6a45) |
| 3 | 5ed6a45 | verified; Skye honestly reported the flight unavailable and offered alternatives; no change (the script never named a flight) -> the script now names a real later flight (b24cf5e) |
| 4 | b24cf5e | verified, searched, change_flight failed on the argument name `new_flight_id` -> synonyms (a9601f8) |
| 5 | a9601f8 | verified, searched, change_flight called once; Skye then asked about the fee, and at the end said the flight was changed. **Whether it was changed cannot be confirmed** from the recorded metadata (by design, arguments and results are not stored) |

In all five, the **first call was warm on Skypiea thanks to the typing hint plus the Enter
re-send** (find_customer 31-38 ms vs 1,470-2,413 ms on the baseline). This is the first time
the hint helped the first call. These are smoke runs, n = 5, not a measurement.

## Known issues

- **The 8B model can claim actions that did not happen** (run 2, possibly run 5), despite the
  prompt's "never invent" rule. The tools refuse invalid changes, but the model's text is not
  checked.
- The model sometimes writes tool calls as text (recovered and flagged) and calls
  `find_customer` before it has a name.
- Skypiea Air's database lives in the console process: restarting the console resets every
  session.
- `make live` needs the real bundles (`BUNDLES_DIR=models`, present on this laptop) until the
  v1.2 release exists.
- The lead-time results (`results/live_console_leadtime/`) were recorded before the Enter
  re-send and are unchanged.

## State of the cluster and console (12:10 UTC)

- kind cluster `skypiea`; `skypiea-base` (3 pods) and `skypiea-pred` (6 pods); images
  `6781fe2`, predictor `console-f141d07` (profile support, lookahead 1); console mapping on
  both stacks.
- Console at http://127.0.0.1:8090 (pid in `.live/console.pid`), demo Skypiea Air (Skye),
  hint off, mirror concurrent, baseline scale-to-zero. Grafana at :3001. Port-forwards are
  supervised (`.live/pf/*.log`).
- Restart: `BUNDLES_DIR=models make live`. Stop: `make live-clean`.

## Next steps for Phase B (UI)

1. A customer-facing chat view for Skye (Skypiea Air look: fictional, no real brands). Keep
   the two-stack columns as an "under the hood" panel.
2. A **booking state panel** fed by the session database (new read-only endpoint, e.g.
   `/api/skyair/state`: the verified customer, and their bookings before and after). This
   makes Skye's claims checkable on screen (the run-5 problem). Optionally flag "claimed but
   not done" by comparing the final answer with the tool results.
3. A persona picker built on `/api/personas`, with copy-to-input buttons for the suggested
   requests.
4. Show the hint sources per turn (open / typing / enter) and the "warmed by hint" tag more
   prominently. Turn the "unknown tool" note into a visible badge.
5. Large-type, projector-friendly layout; keep the `?snapshot=1` view for screenshots.
6. Then a short live smoke (one change-flight conversation) and screenshots.
