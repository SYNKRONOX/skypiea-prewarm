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

Next: STEP 2 (India world, in progress, uncommitted: agent/skyair/world.py, tools.py) and STEP 3 (console/guards.py, engine STATE block, gating, validation, confirmation, UI).
Blockers: none.
