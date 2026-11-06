# INVALID: discarded verification runs (first mirror design)

These two conversations (support, hint off / on; 2026-11-06, commit 42d701c) ran with the first
mirror design: before the LLM's next step the console waited for BOTH stacks. A cold, slower
baseline call therefore gave the predictive stack's warm-up its extra time as free lead time
(e.g. reservation_lookup on the predictive stack: 2,486 ms with the hint off, 484 ms with the
hint on, although the hint does not warm travel_booking). They are kept only as evidence of why
the design changed (gap-matched baseline lane, commit 1fe5fdf). Do not quote them.
