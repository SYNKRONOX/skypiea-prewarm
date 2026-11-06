# User-visible changes, W1 demo polish (branch feat/demo-polish)

Each line: what a viewer of the Live Console (or a reader of the docs) sees differently.

| # | Change |
|---|---|
| 1 | Live summary: idle pod-seconds now update every 2 s (also between calls and after the last call), so the "kept pods warm ... longer" part reflects pods that are warm right now. When nothing has been counted yet it says "No idle pod-seconds measured yet"; units are "pod-s"; both stacks' values are shown. Totals from a previous conversation are no longer shown after Reset. |
| 2 | Gateway (FR-9): `held_ms` now means the hold itself (never above 150 ms); new `hold_late_ms` and `hold_wait_ms` per call (session record), header `X-Skypiea-Hold-Late-Ms`; console CSV gains `base_hold_late_ms`, `pred_hold_late_ms`. `python -m gateway.hold_report` explains the recorded AWS values. PRD_STATUS FR-9 row rewritten. |
| 3 | FR-6: `python -m services.predictor.reload_check` (or `make -f Makefile -f mk/console.mk fr6-reload`) reloads the real model under load; result file `results/fr6_reload_check.json`; PRD_STATUS FR-6 row updated. |
| 4 | A Skypiea call that was warm without being predicted now says why ("still up after an earlier call", "an earlier turn-start hint/prediction had warmed it, e.g. before the predictor was killed", "left over from before"); CSV column `pred_warm_reason`. Kill predictor before the first message drains and starts a new conversation. |
| 5 | Skye uses the customer's only active booking when no code is given; with several she must ask; wrong codes return the customer's valid codes. |
| 6 | Skye gets a "customer sidebar" (bookings, fare, fee rules) after verification / a new booking / a change; she is told to quote prices only from tools. |
| 7 | New customers: a name that is not found can book a trip (search, quote, confirm, new fictional booking code), then check, change, add baggage, cancel, apply offers. Reset clears it. |
| 8 | Offers: `list_offers`, `apply_promo` (fictional codes SKYWINTER10, BAG35, BIZ25), labelled "unknown tool for the predictor ...: no prediction, normal behaviour". |
| 9 | Out-of-scope questions: Skye says what she can help with and does not answer. |
| 10 | Truth check also flags money amounts no tool returned ("Not from a tool: $X"). |
| 11 | Booking card is a small boarding pass (flight, times, seat, fare, status), with "old → new" change lines and "new customer" / "new" badges. Out-of-vocabulary tools get a dashed pill "(unknown to the predictor: no prediction)". Price badges appear under Skye's reply. |
| 12 | Header "Model" dropdown: local Ollama models that support tools (others greyed out), plus a hosted endpoint if configured; switching starts a new conversation; the footer always names the model; the turns CSV gains `llm_model`. |
| 13 | Code-fix demo: "Dev assistant" works on a small mock package `sortkit` (merge_sort with a planted bug, quick_sort, binary_search, tests); tools search / read file / edit file / run tests; tests run in a **simulated test runner** (parsed, never executed). A Repository card (files changed, tests failed → passed) replaces the booking card; the persona card lists suggested tasks; questions outside the repository get "That is not in this repository". |
| 14 | Story mode: "Story: Act 1 / Act 2 / Act 3" buttons (one "Act" for the code fix) play prepared lines against the live model; Act 3 kills the predictor after the first turn and restarts it at the end. |
| 15 | New personas in the Personas list: "A new customer" (Skypiea Air); suggested tasks (code fix). |
| 16 | Docs: `docs/LIVE_DEMO.md` rewritten (what is mock vs real, models and the training data of each profile, Skye's behaviour, truth check incl. prices, warm reasons, code-fix demo and simulated runner, story mode, bake-off table, known limits); `docs/DEMO.md` 5-minute and 1-minute presenter scripts around story mode; `docs/VIVA.md` section 3 (demo) updated, incl. FR-6/FR-9 answers. |
| 17 | Screenshots `docs/img/live_demo_light.png`, `live_demo_dark.png` (re-taken from a live Act 1), new `docs/img/live_demo_codefix.png`; backup set `docs/live_backup/w1/`; results `results/live_bakeoff{,_v2,_v3}/`, `results/live_stories/`, `results/fr6_reload_check.json`. |
