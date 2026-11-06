# Final documentation pass (branch feat/final-docs)

Autonomous run, 2026-11-06, from `main` 9d62db2. Docs only: no code, config, result or
Makefile change; no AWS; nothing pushed, merged or tagged. `tools/pre-commit.sh` (modified)
and the untracked model files in the working tree are not mine and were not committed.

## What changed

| File | Change |
|---|---|
| `README.md` | Reordered for a grader: what Skypiea is, contents line, Architecture, **How to run** (Quickstart, demos, Live Console, AWS, make targets), Results, Limits, Repository layout, **Team** (moved to the end). New: grader steps `make fetch-models` then `make live`; the plain statements (real tool containers and cold starts; mock airline database inside the console; Skye is a local 8B model that can claim actions it did not take, truth panel); a "Demo runs on kind (NOT results)" section with the live-agent table, the Live Console verification runs and the 12-cell lead-time grid; two demo-run limits; `make fetch-models` and `make live-pf` rows. A reserved HTML comment marks where the other session adds its `docs/LIVE_DEMO.md` link line |
| `docs/VIVA.md` | Removed the outdated "DEMO.md/AGENT.md still being written" note; section 3 now leads with the Live Console and the presenter script in `docs/DEMO.md`; fallback points to the committed backups (`docs/live_backup/`, `docs/demo_transcript.log`); 4.8 corrected and extended (LLM behaviour, early connect not measured, v1.2 Enter re-send smoke runs); new 4.9 (console verification runs); section 5 demo-run limits; three rows in the "why this number" table; five extra Q&A; slide 8 |
| `docs/numbers_and_sources.md` | Wording of the 2.7/5.4 s and 5.5/9.0 s rows (see fixes) |
| `CHANGELOG.md` | New: v1.0-submission, v1.1, v1.2 (lead time merged; support console in progress, merged if ready) |
| `docs/README.md` | New: index of every doc with one line each (a reserved comment for `LIVE_DEMO.md`) |

## Checks and fixes

Links: every relative link and anchor in `README.md`, `docs/VIVA.md`, `CHANGELOG.md` and
`docs/README.md` was checked with a script against the tree. All resolve, except (by design)
these code-formatted references to files that exist only on branch `feat/console-support`,
labelled as such: `docs/handoff/PHASE_A_HANDOFF.md`, `docs/MODELS_ATTRIBUTION.md`.

Numbers re-checked against the source files:

| Number | Source | Result |
|---|---|---|
| AWS A/B predictive and scale-to-zero cold %, idle pod-s, p99 | `results/scenario_aws_summary.csv` | match |
| E0 per-tool medians/p95 (spot: run_tests, user_lookup, refund_issue; kind and AWS; 3 at once) | `results/e0_aws_summary.csv`, `results/e0_kind_summary.csv`, `results/e0_aws_p3_summary.csv` | match |
| Live agent table and think-gap shares (7.9% / 0.5%, 203 calls) | `results/agent_runs/summary.csv`, `summary_think.csv` | match |
| Hideable 91.1/67.8, 79.1/56.0, 78.1/56.1 | `results/sim_hideable.csv` (AWS rows) | match |
| Lookahead margins (+25.75, +3.56/+3.02, +15.66, +4.30/+3.72) | `results/sim_lookahead_val.md`, `_test.md` | match |
| Lead-time grid, first-call ranges, 4-11x idle | `results/live_console_leadtime/tables.md` | match |
| Console verification runs, 1,856 vs 2,920 ms, 32 vs 2,504 ms | `results/live_console_demo/runs.md` | match |
| Load test p99 13.2 / 15.9 ms | `results/loadtest.csv` | match |
| FR-2 86.8 / 90.9 on 76 / 44 calls | `docs/results_summary.md` | match |
| `make live` 44 s / 2 min 47 s | `docs/DEMO.md` | match |

Fixes made:

1. **"2.7 s / 5.4 s, median over tools" and "5.5 s / 9.0 s"** (VIVA pitch and "why this
   number" table, `docs/numbers_and_sources.md`): these are medians of all E0 runs pooled over
   tools (2,734 / 5,360 ms in `results/e0_aws_runs.csv`, 160 runs each; 5,541 / 9,020 ms in
   `results/e0_aws_p3_runs.csv`, 180 each). The median of the per-tool medians is different
   (2,732 / 5,197 ms; 3 at once 5,432 / 8,402 ms). Wording and sources corrected; the numbers
   stand.
2. **README think-gap median** "1,354 ms / p90 2,938 ms" changed to the file's 1,354.5 /
   2,937.5 ms.
3. **VIVA 4.8** "turn 3 was warm only in airline with lookahead 1": one lookahead-2 cell had
   1/3 warm. Corrected.
4. **VIVA** outdated "DEMO.md and AGENT.md were still being written", "the infra branch is
   building both", "confirm the transcript is committed": replaced (all are merged and
   committed).

## Said plainly, and open points

- **The truth panel is not committed on any branch I could see** (`git grep truth` on `main`
  and `feat/console-support` finds nothing in `console/` or `demo/`; the Phase A handoff lists a
  "booking state panel" as a Phase B next step). The README describes it as asked, inside the
  v1.2 paragraph, and adds that it was not yet committed at the time of writing. Remove or
  confirm that sentence when v1.2 is merged.
- **`make fetch-models` with the release URL exists only on `feat/console-support`.** On `main`
  it needs `MODELS_URL` / `MODELS_SHA256`; the README says so. The Phase A handoff recorded that
  the v1.2 release returned 404 (12:10 UTC); I could not re-check (no `gh` here, no network
  calls made). Until the release exists, the grader path in the README stops at
  `make fetch-models` with its "not published" message.
- The Skypiea Air sizes (50 customers, 150 bookings, 336 flights) are on the branch only, so I
  did not quote them in the README.
- `docs/aws_plan.md`'s title still says "not applied"; the index line explains it is the plan
  as reviewed before apply. Not edited.

## Second batch (deadline moved; stop at 18:00 UTC)

| Commit | What |
|---|---|
| aa45f8a | `PRD.md` at the root, copied byte-for-byte (`cmp` identical) from the given path; no e-mail address found in it |
| 3439c71, b0dfe40 | `docs/PRD_STATUS.md`: FR-1..9, NFR-1..6, the PRD's E1..E5 and 15 scope/stack items, each with status, evidence and one line |
| b0dfe40 | `dev.md`: developer guide |
| this commit | README links to `PRD.md`, `docs/PRD_STATUS.md`, `dev.md` near the top; `docs/README.md` entries; `shaper/README.md` and `tools/README.md` said "Status: not started" (stale) and now point to the gateway and `dev.md` |

PRD_STATUS points a reviewer should know:

- **Not done:** NFR-4 (500-session soak; a 40-session AWS check instead), NFR-5 (no reconcile
  timing, one node).
- **Changed:** NFR-1 (service-alone load test, not end-to-end at 500 sessions, no Locust),
  OpenFaaS (own operator; OpenFaaS CE cannot scale to zero), LangGraph (own agent loop and
  replayer), 6-8 tools (16), real traces in addition to synthetic.
- **Needs check:** FR-6/E5 (no committed reload duration against 2 s), FR-9 (the AWS runs'
  `held_ms_max` reaches 397.2 ms, measured from arrival; the hold wait itself is bounded by
  150 ms in code, but "never exceeds the cap" should not be claimed until that is explained).
- The PRD's E1-E5 differ from the E ids in `docs/experiments.md` and the README; PRD_STATUS
  says so and uses the PRD's definitions.
- The PRD's Figure 1 note links `dev.md`; that file now exists.
