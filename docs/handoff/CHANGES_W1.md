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
