# Decisions, week 2 red-team hardening (branch `feat/redteam-ready`)

Autonomous session. Each choice follows, in order: honesty (claims match the code), safety and
reversibility, done before the freeze, clearest for a stranger.

| # | When (UTC) | Decision | Why |
|---|---|---|---|
| D1 | 2026-11-06 17:30 | The worktree had an uncommitted edit to `tools/pre-commit.sh` (chat text pasted over its first line, which broke the shebang). Saved it outside the repo (session scratchpad: `pre-commit-uncommitted.patch`) and restored the committed file before branching. The untracked `model_second_v1.0.txt` and `phase5,6_model.txt` are left alone and never added. | The edit looked accidental and made the hook unrunnable. Saving a copy keeps it recoverable. |
