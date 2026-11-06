# Console support report (branch feat/console-support, Phases A and B)

Phase B: autonomous run, 2026-11-06, 15:14 to about 16:00 UTC. Phase A: `PHASE_A_HANDOFF.md`.
Nothing was pushed, merged, tagged or published. No AWS was used. No package was added.
Decisions: `docs/handoff/DECISIONS.md` (#1-#9 Phase A, #10-#19 Phase B).

## What changed in Phase B

| Commit | What |
|---|---|
| 622a660, 848564d | **Truth panel backend** (`console/truth.py`). Every write tool call (change_flight, cancel_booking, issue_refund, add_baggage) is logged as success, quote (asked for confirmation, nothing written) or refused, from the database's own result. After each final Skye reply, regex rules find "done" claims and compare them with the writes that succeeded in that turn. A disagreement gives the badge "Not done: ..." (the turns CSV gets `truth_claims`, `truth_verdict`, `truth_badge`, `truth_note_sent`, `truth_corrected`). One system note is then added for the model, and whether the next reply corrected itself is logged. Skye's text is never edited. `GET /api/skyair/state`. `ScriptedStreamingLLM(answers=...)`. Tests: `tests/test_truth.py` (agreeing claim, disagreeing claim with badge, note and correction, no edit of the text, CSV, claim phrasing cases, state, endpoint) |
| b061a08, 551268f, 81e7174, 8d4dce9 | **UI** (hand-written HTML/CSS/JS, system fonts, no CDN, light and dark). Header: Skypiea Air wordmark (original SVG), "Live Console", demo, Normal policy, hint (off / at Enter / on typing), Kill / Restart predictor, health dot (cluster, predictor, LLM), Details, theme, "?". Left: chat bubbles, typing indicator, tool pills, **Booking record** with the write log, Send / Reset / Autopilot, Personas (from `/api/personas`; click a request to fill the input). Right: a one-sentence live summary with a time-saved counter that can go negative, Normal and Skypiea rows in plain words with warm/cold words and icons (click a row for one explanation sentence), and a "Next likely ... → warming ..." ticker with a spinner while a Skypiea pod starts. Details drawer: totals, pod grids, predictor top-3, turns and hint lead times, abandoned turns, think window, lookahead / mirror / replay, vocabulary note, truth-check rules, exports, Grafana. "?" overlay: 3 steps, the cost caveat, the setup note. The chat is restored after a reload or in `?snapshot=1`. `?details=1` and `?help=1` are for screenshots |
| 133aaca, 8b4dfac | Autopilot scenarios (`agent/tasks/skye.py SCENARIOS`, `POST /api/autopilot {"scenario"}`), `demo/console_run.py --scenario`, `demo/verify_phase_b.sh`, `demo/phaseb_table.py` |
| 1da4256, eab56d6 | Docs: `docs/LIVE_DEMO.md`; `docs/DEMO.md` with the 5-minute and 1-minute presenter scripts (old anchors kept); **one** README line linking `docs/LIVE_DEMO.md`; screenshots `docs/img/live_demo_{light,dark}.png`; backup set `docs/live_backup/phase_b/` |

## Verification

- **Fresh clone** (`.live/fresh`, inside the repo and git-ignored, cloned at 133aaca, with a new venv; DECISIONS #18):
  `make setup` OK; `make test` **431 passed, 1 skipped**; ruff clean; `make check-sizes` OK.
  Later commits changed UI wording, docs and one log string (`console/truth.py describe`).
  The final tree was tested again in this worktree (see "Final state").
- **`make fetch-models`** in the fresh clone: exit 2, "release does not exist yet", as
  designed. **`make live`** from the fresh clone with `BUNDLES_DIR=<repo>/models`: see the
  "make live" line under "Final state". Note: in a clone whose `origin` is a local path (like
  `.live/fresh`), fetch-models builds a malformed GitHub URL (`https://github.com//home/...`)
  before failing with exit 2. A clone from GitHub derives owner/repo correctly (Phase A test).
- **Verification matrix** (`demo/verify_phase_b.sh`, 15:24-15:44 UTC). Llama3.1:8b, autopilot customer
  with a typing model of 200 characters/min (an assumption), Normal = scale-to-zero, mirror concurrent.
  **These are demo runs, one conversation each, not results.**

| Run | Calls ok (Normal / Skypiea) | Waited on tools: Normal | Skypiea | Cold N / S | Skypiea idle pod-s (Normal) | Writes (outcome) | Truth verdicts per turn |
|---|---|---|---|---|---|---|---|
| ambiguous_name, hint off | 3/3 / 3/3 | 7.47 s | 5.48 s | 3 / 2 | 70.6 (12.8) | none | all no_claim |
| ambiguous_name, typing | 3/3 / 3/3 | 7.21 s | 3.20 s | 3 / 1 | 96.7 (13.1) | none | all no_claim |
| cancel_refund, hint off | 5/5 / 5/5 | 12.50 s | 5.47 s | 5 / 2 | 126.4 (22.6) | cancel success, refund success | t3-t5 agree |
| cancel_refund, typing | 5/5 / 5/5 | 12.69 s | 3.67 s | 5 / 1 | 169.6 (22.8) | cancel success, refund success | t3, t4 agree |
| change, hint off | 6/6 / 6/6 | 15.93 s | 12.94 s | 6 / 5 | 123.2 (29.2) | change refused | all no_claim (Skye apologised; no false claim) |
| change, typing | 6/6 / 6/6 | 14.51 s | 5.50 s | 6 / 2 | 214.0 (28.4) | change refused | **t5 not_done: badge** |
| change, typing, **predictor killed** | 6/6 / 6/6 | 13.22 s | 8.21 s | 6 / 3 | 138.2 (29.4) | change refused | **t5 not_done: badge** |
| failed_verification, hint off | 5/5 / 5/5 | 12.80 s | 5.80 s | 5 / 2 | 118.3 (23.1) | change refused (not verified) | all no_claim |
| failed_verification, typing | 5/5 / 5/5 | 11.71 s | 3.69 s | 5 / 1 | 164.5 (23.3) | change refused | all no_claim |
| unknown_tool, hint off | 3/3 / 3/3 | 7.31 s | 6.32 s | 3 / 2 | 76.1 (13.3) | change refused | all no_claim |
| unknown_tool, typing | 3/3 / 3/3 | 7.44 s | 0.11 s | 3 / 0 | 106.0 (12.6) | change refused | all no_claim |

What these show (functional checks):
- **Every call succeeded on both stacks in all 11 runs, including with the predictor killed.**
- **The truth badge fired without anyone forcing it** in 2 runs. In both, change_flight was refused
  because the model sent no booking code. Skye's last reply still said "I changed your flight to SA383 ...".
  Both badges were true positives (the booking record shows the old flight).
  `truth_corrected` stays empty because the conversation ended on that turn, so there was no next reply.
  No badge fired on a reply that agreed with the record. The cancel and refund claims matched real writes.
- In the 5 "change" conversations the 8B model never completed a valid flight change. It sent
  an empty or literal booking code, or a made-up old flight. The tools refused each time.
- **unknown_tool**: the model did NOT invent a tool, so the unknown-tool path was not exercised
  live (the unit tests cover it). **ambiguous_name**: Skye asked again and then handed over.
- Skypiea idle pod-seconds were 3-8x Normal's in every run. That is the cost side, and the
  summary sentence shows it.

## Open issues

1. **Predictor-kill run, unexplained warm calls.** Skypiea's first three calls (user_lookup)
   were warm (33-38 ms) while the predictor was unreachable, and the gateway marked them "not predicted".
   The gateway can only warm through the predictor (checked `gateway/app.py hint`). The likely cause is a
   pod warmed before the kill took effect (the conversation-open hint at Reset), but this is **not verified**.
   The other three calls were cold, as expected without a predictor.
2. The truth check uses regex rules. It can miss unusual phrasings and only covers the four write kinds.
   It does not catch other invented facts. Example: Skye quoted a "$50 change fee" for a Business fare,
   which has no fee.
3. The 8B model often fails to complete valid changes (argument errors). The tools refuse
   correctly, but the "change a flight" story may end in a handover during a live talk. Script
   lines that name the booking code and flight help.
4. The "progress ring" is an indeterminate spinner while a pod is starting, not a filling ring
   (DECISIONS #14).
5. There is no early-connect toggle in the console, so none is shown.
6. Screenshots in `docs/live_backup/phase_b` show "?" in one write-log line. Later code writes
   "(no booking code given)" there.
7. The Skypiea Air database lives in the console process, so a restart resets every session (unchanged).

## Merge (for you; nothing was merged here)

```bash
git checkout main
git merge --no-ff feat/console-support -m "Live Console: Skypiea Air demo (Skye), truth panel, presentation UI"
make test && .venv/bin/ruff check . && make check-sizes
git push origin main          # your decision
```

## Release v1.2 assets (built locally, NOT published)

| Asset | Size | sha256 |
|---|---|---|
| `dist/skypiea-models-af000231.tar.gz` | 1,003,524 bytes | `918b36c5409ffaf261e37326cc290125ab99a69b56569c215dff7687989bdf23` |
| `dist/SHA256SUMS` | 97 bytes | `784d237552b6fa3128783b5f4296d456658d4c9750ec47a25171cf2cd4226048` |

To publish: create GitHub Release `v1.2` on SYNKRONOX/skypiea-prewarm and attach both files.
After that, `make fetch-models` works without `BUNDLES_DIR`.

## Final state

- **make live** from the fresh clone (`BUNDLES_DIR=<repo>/models`, because Release v1.2 does
  not exist yet): exit 0, "Skypiea Live Console is up (22 s)" (stacks already installed).
- This worktree, final tree: `make test` **432 passed**; ruff clean; `make check-sizes` OK
  (638 tracked files).
- The console runs from this worktree at http://127.0.0.1:8090 (pid in `.live/console.pid`):
  demo Skypiea Air, **empty conversation**, hint off, Normal = scale-to-zero, predictor up.
  Grafana is on :3001. The port-forwards were restarted by the fresh clone's `make live`. Their
  logs are under `.live/fresh/.live/pf/`, so do not delete `.live/fresh` while they run
  (`make live-clean` or `make live` restarts everything).
- Local run artefacts (git-ignored, contain fictional conversation text): `.live/runs/phaseb_*`,
  `.live/phaseb_verify.log`, `.live/fresh_verify.log`, `.live/fresh_live.log`.
