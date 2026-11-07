feat/demo-polish final commit: 89cba1b, Phase A done: yes
feat/demo-realism GATE: not run

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
| STEP 4 | running | | qwen3:8b with think=false verified (7 vs 1,215 tokens); AFTER suite 3 models x 3 reps started 00:22 UTC -> results/suite/after (log .live/suite_after.log; resume with --resume) |

| STEP 5 (part) | done | b2f82d3.. | Proof panel (tested), docs LIVE_DEMO/MEASURED_VS_SCRIPTED/SKYPIEA_WORLD, compare generator, Auto-play of suite cases for the live smoke |

Next: when the AFTER suite ends: summary, default model, STEP 5 (report, live smoke per scenario, DEMO.md, screenshots, backup, MEASURED_VS_SCRIPTED), gate.
Blockers: none.
