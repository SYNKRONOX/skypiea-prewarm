# Skye scenario suite: correctness runs (W1 night)

**CORRECTNESS runs, not results about latency.** Real agent loop (`console/engine.py`, the same
one the Live Console uses) and a real local LLM through Ollama; tool calls answered in-process
by the mock database; **no cluster and no pods** (correctness does not depend on pod latency).
Customer turns are **scripted** per variant. Wall times in the files are this laptop's LLM
times and are never used for any claim.

- Suite: `agent/suite/scenarios.py` (6 scenarios x 3 messy variants, end-state oracle: the
  final mock-database diff must equal the expected one; no LLM judge). Runner:
  `python -m agent.suite.run --models ... --reps N --out ...`.
- `before/`: code at `c0089f8` (feat/demo-realism start = feat/demo-polish Phase A), the old
  world (six fictional airports SKA..SKF, USD), llama3.1:8b and qwen2.5:7b, 2 reps each, run
  2026-11-06 23:58 to 2026-11-07 00:12 UTC.
- `after/`: code `a8b9693` (synthetic India-centred world, STEP 3 guardrails), llama3.1:8b,
  qwen2.5:7b and qwen3:8b (thinking off), 3 reps each, run 2026-11-07 from 00:22 UTC. Never
  overwrites `before/`. Comparison: `docs/SUITE_BEFORE_AFTER.md`.

The two worlds have different people, places and dates, so a scenario's lines differ between
before and after; the scenario shapes, the variants (typos, wrong email then right, changes
mind, unsupported request) and the oracle rules are the same.
- `final_check/`: llama3.1:8b, 1 rep, on the final code (0e3c4af): 18/18.
