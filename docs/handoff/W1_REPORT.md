# W1 report: demo polish (branch feat/demo-polish)

Autonomous run, 2026-11-06 17:28 to about 23:10 UTC. Branch `feat/demo-polish`, created from
`main` (da67ce7). Nothing was pushed, merged, tagged or published. No AWS was used. No package
was added (no change to requirements.in or the lock). Decisions with their reasons:
`DECISIONS.md` (W1-1 to W1-22). Every user-visible change: `CHANGES_W1.md`. Requests to the
other session: `REQUESTS_W1.md`. **All live numbers below are demo runs, not results**: one
laptop (kind), one conversation per cell, local LLMs.

## What changed

| # | Item | What was found / done | Evidence |
|---|---|---|---|
| P0-1 | Summary "kept pods warm 0.0 s longer" | Cause: totals were computed only when a call record ended, and the idle counters are read every 2 s, so the first call saw 0.0 / 0.0, and pods kept warm during think time or after the last call were never added. Now totals are re-published after every 2 s poll, Reset reads the counters fresh, stale totals of a previous conversation are ignored; wording "No idle pod-seconds measured yet" / "pod-s ... so far". | `tests/test_console_idle.py`; recorded event logs `phaseb_change_off` (0.0/0.0), `final_smoke` (1.8/1.8 three times) |
| P0-2 | FR-9 `held_ms` up to 397.2 ms | It was measured from the call's ARRIVAL to the moment the event loop RESUMED the call after `wait_ready(timeout=150 ms)`. Now three measured fields: `held_ms` (the hold itself, <= cap by construction; histogram too), `hold_late_ms`, `hold_wait_ms` (the old meaning). AWS records unchanged and explained: 675 held calls, overrun above 150 ms p50 1.0 ms, p90 2.1 ms, p99 24.0 ms; the 397.2 ms call is a lone outlier (no other call within 0.3 s; cause not verified); the 7 calls above 170 ms had a median of 4 other calls arriving within 0.3 s. | `gateway/app.py`, `gateway/hold_report.py`, `tests/test_gateway_hold.py` (incl. a blocked event loop and 8 concurrent holds); PRD_STATUS FR-9 row |
| P0-3 | FR-6 reload | `services/predictor/reload_check.py`: the real predictor in-process, 8 client threads, three reloads of real bundles, plus one request per reload kept open for the whole reload (body sent in two parts). Each reload 0.49-0.50 s in the service (client 0.50-0.51 s), budget 2 s; 323-357 requests overlapped each reload; 0 non-200 of 3,754; every spanning request answered 200 by the new model. Laptop, not AWS. | `results/fr6_reload_check.json`, `tests/test_reload_check.py`; PRD_STATUS FR-6 row |
| P0-4 | Predictor-kill "warm but not predicted" | Verified from the Phase B event log: the typing-mode hint sent at Reset got an `ok` decision 0.9 s before the kill took effect and warmed user_lookup; later decisions failed (which clears the gateway's `last_warm`); the pod stayed warm because warm time counts from the last use. Now every warm-but-not-predicted call names its cause (kept after use / earlier decision / already up; matched by server), never "predicted"; a kill before the first message drains and restarts cold. | `tests/test_console_warm_reason.py`; CSV `pred_warm_reason` |
| P0-5 | One active booking | Booking tools use the verified customer's only confirmed booking when the code is missing; with several they return the codes and "ask which one"; wrong codes list the customer's codes (never before verification). | `tests/test_skyair_w1.py` |
| P1-6..11 | Skye realism | Customer sidebar (in the system prompt, replaced in place); new-customer flow (`book_flight`, duplicate guard); offers (`list_offers`, `apply_promo`, labelled "unknown tool for the predictor: no prediction"); out-of-scope rule; price truth check ("Not from a tool: $X"); boarding-pass card with old → new lines and "new customer" badge; `find_customer` verifies when the customer gives email or code with the name (same proof rule). | `tests/test_skyair_w1.py`, `docs/LIVE_DEMO.md` |
| P1-12 | Model dropdown | Local Ollama models with the `tools` capability (+ optional hosted endpoint, key never shown); switching resets; model in the footer and every calls/turns row. | `tests/test_console_models.py` |
| P1-13 | Bake-off | Five personas x 2 reps x 2 models, run three times as fixes landed (table below). Default stays llama3.1:8b, chosen by correctness. | `results/live_bakeoff{,_v2,_v3}/` |
| P2-14/15 | Code-fix demo | Dev assistant on the mock `sortkit` package (planted merge_sort bug); search / read / edit / run tests; **simulated test runner** (ast only, literal asserts, "not evaluated" when it cannot judge; nothing is executed); Repository card with tests failed → passed and the diff; suggested tasks; out-of-repo answer. | `tests/test_devrepo.py` |
| P2-16 | Story mode | Act 1/2/3 (Skypiea Air) and one code-fix Act; prepared lines to the LIVE model; Act 3 kills the predictor after turn 1 and restarts it. | `tests/test_console_story.py`, `results/live_stories/` |
| 17 | Docs | `docs/LIVE_DEMO.md` (mock vs real, models and training data, bake-off), `docs/DEMO.md` (5- and 1-minute scripts around story mode), `docs/VIVA.md` section 3, PRD_STATUS FR-6/FR-9 rows. | |

## Bake-off (demo runs; n = 10 conversations per model per version)

| | llama3.1:8b v1 / v2 / v3 | qwen2.5:7b v1 / v2 / v3 |
|---|---|---|
| Task completed | 8 / 6 / 9 | 6 / 8 / 8 |
| First-try valid calls (mean) | 0.88 / 0.84 / 0.93 | 0.87 / 0.93 / 0.93 |
| Booking codes invented by the model | 4 / 5 / 4 | 2 / 0 / 0 |
| False "done" claims (true "Not done" badges) | 0 / 1 / 1 | 4 / 2 / 2 |
| Prices no tool returned | 0 / 0 / 3 | 0 / 0 / 4 |
| Unasked hand-overs (of 8 allowed-to-avoid) | 8 / 8 / 8 | 0 / 0 / 0 |
| Calls written as text | 0 | 0 |

v1 = W1 tools; v2 = + prompt nudge (W1-18); v3 = + find_customer verifies with a given proof
(W1-20, current). Footnotes (customer-typed codes subtracted in v1, 3 qwen regex false
positives not counted, think times) are in `docs/LIVE_DEMO.md`. **Default: llama3.1:8b**
(unchanged): 23 vs 22 of 30 tasks and 2 vs 8 false "done" claims over the three versions; the
completion difference is within run-to-run variance.

## Story acts and checks (demo runs, current code, one conversation each)

| Run | Calls ok Normal / Skypiea | Waited on tools Normal / Skypiea | Idle pod-s Normal / Skypiea | Outcome |
|---|---|---|---|---|
| Act 1, llama3.1:8b | 6/6 / 6/6 | 14.5 s / 12.5 s | 28.6 / 210.4 | booked, duplicate refused, bag added; "Not from a tool: $114" (her own sum) |
| Act 1, qwen2.5:7b | 3/3 / 3/3 | 6.6 s / 2.4 s | 15.8 / 170.8 | only a quote; told the customer it was booked → "Not done: no booking was made" |
| Act 2, llama3.1:8b | 4/4 / 4/4 | 9.5 s / 5.4 s | 18.8 / 195.5 | change sent without the code the customer named → refused "which booking" |
| Act 2, qwen2.5:7b | 3/3 / 3/3 | 6.2 s / 5.2 s | 15.8 / 176.1 | flight changed |
| Act 3, llama3.1:8b | 4/4 / 4/4 | 10.0 s / 6.0 s | 21.1 / 115.3 | bag added (one booking, no code needed); predictor killed after turn 1; every call succeeded |
| Act 3, qwen2.5:7b | 3/3 / 3/3 | 7.1 s / 2.1 s | 15.8 / 109.5 | bag added; predictor killed; every call succeeded; "Not from a tool: $731" twice |
| Code fix, llama3.1:8b | 10/10 / 10/10 | 11.8 s / **38.8 s** | 31.9 / 199.5 | 6 edits failed ("old text not found"); tests unchanged (4 passed, 2 failed); Skypiea waited longer in this run |
| Code fix, qwen2.5:7b | 5/5 / 5/5 | 10.9 s / 7.9 s | 24.9 / 175.6 | appended a rewrite of merge_sort → 3 "not evaluated" (runner cannot judge) |
| Out of scope (weather, write code), both models | no tool calls | – | – | both declined and listed what Skye can do (chat saved in the state files) |

Every tool call of every W1 run succeeded on both stacks (396 calls), including after the
predictor was killed. Skypiea's idle pod-seconds were 5-11 times Normal's in every run (the cost side).
Earlier versions of these runs are kept with READMEs (`results/live_stories/v1`, `v2`,
`v3_act2_unanswered`, `superseded`).

## Verification

- Worktree, final tree: `make test`, ruff and `make check-sizes`: see "Final state".
- **Fresh clone** (`.live/fresh_w1`, inside the repo, git-ignored, cloned at b1d7f53, new
  venv): `make setup` OK (45 s); `make test` **473 passed, 2 skipped** (2 min 14 s); ruff
  clean; `make check-sizes` OK (1,034 tracked files). `BUNDLES_DIR=<repo>/models make live`:
  exit 0, "up (101 s)"; it rebuilt the images from cache (tag 99f90a2) and redeployed both
  stacks. Its console could not bind :8090 at first (this worktree's console was running, so
  the "up" check was answered by that one); restarted from the clone, it served one live
  cancel_refund conversation (3/3 calls on both stacks, cancellation written).
- Screenshots `docs/img/live_demo_{light,dark,codefix}.png` and the backup set
  `docs/live_backup/w1/` come from live story runs (README there).

## Open issues

1. Small models: llama3.1:8b invents booking codes, hands over unasked, and cannot produce an
   `edit_file` the code-fix tool accepts; qwen2.5:7b claims bookings that did not happen.
   The tools refuse and the truth check flags; the story can still end without the goal.
2. The code-fix demo never reached "tests passed" with either model in W1 runs; the runner
   says "not evaluated" for qwen's rewrite. Use it as an optional extra, or type the fix.
3. Truth checks are regular expressions: one false positive was found and fixed (W1-21);
   others may exist. The price check flags sums Skye computes herself, by design.
4. The hint setting switched to "off" during the backup Act 1 run by a request none of my
   scripts sent (not identified; `docs/live_backup/w1/README.md`). The other session's
   checkout also runs port-forwards to the same cluster, so it may use the same console or
   stacks; not verified.
5. Story / bake-off runs before ~22:50 UTC ran on the pre-W1 gateway image (W1-22).
6. `mk/console.mk` is not included by the root Makefile yet (R1).
7. `fetch-models` still needs Release v1.2 to be published (unchanged).

## Requests for the other session

See `REQUESTS_W1.md`: R1 include `mk/console.mk` in the Makefile; R2 PRD_STATUS E5 row → FR-6
evidence; R3 document the FR-9 fields in `docs/gateway_controller.md`; R4 add `w1/` to
`docs/live_backup/README.md` and mention the optional `ollama pull qwen2.5:7b` in README's
live-demo line.

## Merge (for you; nothing was merged here)

```bash
git checkout main
git merge --no-ff feat/demo-polish -m "W1 demo polish: P0 fixes (summary idle, FR-9 hold metric, FR-6 reload check, warm reasons, single booking), Skye realism, model dropdown + bake-off, code-fix demo, story mode"
make test && .venv/bin/ruff check . && make check-sizes
git push origin main          # your decision
```

## Final state

- The console runs from this worktree on http://127.0.0.1:8090 (pid in `.live/console.pid`):
  Skypiea Air, llama3.1:8b, empty conversation, hint off, Normal = scale-to-zero, predictor up.
- Both stacks were (re)deployed by the fresh clone's `make live`. Port-forward restart loops
  are running from this worktree, `.live/fresh`, `.live/fresh_w1` and the main checkout
  (`skypiea-prewarm`, the other session); they compete for the same loopback ports and one of
  each binds. Do not delete `.live/fresh` or `.live/fresh_w1` while their loops write logs
  there; `make live-clean` (or `make live`) restarts them cleanly.
- 396 tool calls were recorded in the W1 result and backup CSVs; all 396 succeeded on both
  stacks.
