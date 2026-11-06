# Requests from the W1 demo-polish session (branch feat/demo-polish) to the other session

These files are not mine to edit. Each item says what to change and why.

| # | File | Request | Why |
|---|---|---|---|
| R1 | `Makefile` | Add `include mk/console.mk` after `include mk/demo.mk`. | New targets `fr6-reload`, `hold-report` (and later console targets) live there. Until then: `make -f Makefile -f mk/console.mk <target>`. |
| R2 | `docs/PRD_STATUS.md` E5 row | Point to the FR-6 row's new evidence: `results/fr6_reload_check.json` (reload 0.49-0.50 s under load, in-flight requests served). Status can become "Met (prepared bundle, laptop)". | Only the FR-6 and FR-9 rows are mine. |
| R3 | `docs/gateway_controller.md` | Document the FR-9 fields: `held_ms` (the hold itself, <= cap), `hold_late_ms`, `hold_wait_ms` (the old meaning of held_ms), header `X-Skypiea-Hold-Late-Ms`. | Gateway docs are not mine. |
