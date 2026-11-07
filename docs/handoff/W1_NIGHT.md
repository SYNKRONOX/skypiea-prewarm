feat/demo-polish final commit: 89cba1b, Phase A done: yes
feat/demo-realism GATE: PASS (default llama3.1:8b correctness suite 22/36 = 61% before -> 54/54 = 100% after, 18/18 on the final code; make test 503 passed; ruff clean; check-sizes OK; fresh clone: setup OK, 501 passed + 2 skipped, ruff, sizes, make live exit 0 + one live conversation OK)

# W1 night run: progress (autonomous)

Started 2026-11-06 23:47 UTC from feat/demo-polish 9ae97a1 (clean). Local models only
(Ollama: llama3.1:8b, qwen2.5:7b, qwen3:8b). Updated after every step.

| Step | State | Commit | Notes |
|---|---|---|---|
| Housekeeping | done | 9d90cc3 | hint change audit trail (W1_REPORT 'W1 night'); console restarted from this worktree |

| Phase A | done | 89cba1b | display strings, MEASURED_VS_SCRIPTED.md, make watch-pods; make test 476 passed, ruff clean, check-sizes OK |

| STEP 1 | done | f39a531 | suite agent/suite/, BEFORE baseline results/suite/before: llama3.1:8b 22/36 (rep 11/18, 11/18), qwen2.5:7b 14/36 (rep 8/18, 6/18). Finding: llama sends customer_confirmed as the STRING "false", which the old tools treated as true (writes without confirmation, e.g. two bags) |

| STEP 2 | done | a8b9693 | agent/skyair/world.py (seed 20270111, today 2027-01-11), docs/SKYPIEA_WORLD.md; old skyair_db.json + generate.py removed |
| STEP 3 a-h | done | a8b9693 | console/guards.py, engine STATE block/gating/validation/confirmation/compact context; UI state box, guardrails, greeting chips; live smoke on the cluster OK (6/6 calls both stacks) |
| STEP 4 | done | ffd53fb | qwen3:8b think=false (7 vs 1,215 tokens); AFTER suite (code a8b9693): llama3.1:8b 54/54 (18,18,18), qwen3:8b 46/54 (15,15,16), qwen2.5:7b 42/54 (16,13,13); default stays llama3.1:8b; docs/SUITE_BEFORE_AFTER.md |

| STEP 5 (part) | done | b2f82d3.. | Proof panel (tested), docs LIVE_DEMO/MEASURED_VS_SCRIPTED/SKYPIEA_WORLD, compare generator, Auto-play of suite cases for the live smoke |

| STEP 5 | done | 3785805, 0e3c4af | live smoke per scenario on the cluster (results/live_night: 6/6 expected end states, 29/29 calls ok both stacks), DEMO.md, screenshots, recorded backup docs/live_backup/night, final-code recheck results/suite/final_check 18/18 |
| Gate | PASS | 5d76bd5 | see first lines; fresh clone .live/fresh_night at 0e3c4af |

Final branch heads: feat/demo-polish 89cba1b (Phase A), feat/demo-realism (this commit). Nothing pushed or merged.
Next: nothing required. Optional: merge (commands in W1_REPORT.md), R1-R5 in REQUESTS_W1.md.
Blockers: none.

# fix/free-typing (2026-11-07, from origin/main 66404ff)

| Step | State | Commit | Notes |
|---|---|---|---|
| 1 text tool calls | done | (next commit) | console/textcalls.py + engine: JSON tool calls in text held back while streaming; offered tool -> real call; unknown/not offered -> hidden, not run, logged "ignored text tool call", one retry with a hint, then a fixed fallback; empty input answered without the LLM |
