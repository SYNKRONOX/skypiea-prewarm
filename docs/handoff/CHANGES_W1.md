# User-visible changes, W1 demo polish (branch feat/demo-polish)

Each line: what a viewer of the Live Console (or a reader of the docs) sees differently.

| # | Change |
|---|---|
| 1 | Live summary: idle pod-seconds now update every 2 s (also between calls and after the last call), so the "kept pods warm ... longer" part reflects pods that are warm right now. When nothing has been counted yet it says "No idle pod-seconds measured yet"; units are "pod-s"; both stacks' values are shown. Totals from a previous conversation are no longer shown after Reset. |
| 2 | Gateway (FR-9): `held_ms` now means the hold itself (never above 150 ms); new `hold_late_ms` and `hold_wait_ms` per call (session record), header `X-Skypiea-Hold-Late-Ms`; console CSV gains `base_hold_late_ms`, `pred_hold_late_ms`. `python -m gateway.hold_report` explains the recorded AWS values. PRD_STATUS FR-9 row rewritten. |
| 3 | FR-6: `python -m services.predictor.reload_check` (or `make -f Makefile -f mk/console.mk fr6-reload`) reloads the real model under load; result file `results/fr6_reload_check.json`; PRD_STATUS FR-6 row updated. |
