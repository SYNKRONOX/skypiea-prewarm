# Decisions, week 2 red-team hardening (branch `feat/redteam-ready`)

Autonomous session. Each choice follows, in order: honesty (claims match the code), safety and
reversibility, done before the freeze, clearest for a stranger.

| # | When (UTC, system clock) | Decision | Why |
|---|---|---|---|
| D1 | 2026-11-06 17:30 | The worktree had an uncommitted edit to `tools/pre-commit.sh` (chat text pasted over its first line, which broke the shebang). Saved it outside the repo (session scratchpad: `pre-commit-uncommitted.patch`) and restored the committed file before branching. The untracked `model_second_v1.0.txt` and `phase5,6_model.txt` are left alone and never added. | The edit looked accidental and made the hook unrunnable. Saving a copy keeps it recoverable. |
| D2 | 2026-11-06 17:32 | `make doctor` is a bash script, not Python. | It must run before Python 3.12 or the venv exist. |
| D3 | 2026-11-06 17:32 | Doctor thresholds: Docker memory 6 GB and free disk 15 GB, both from docs/DEMO.md; busy demo/live ports are a WARN (an earlier run of ours may hold them), the registry port held by a foreign program is a FAIL. | No invented numbers; avoid false FAILs on re-runs. |
| D4 | 2026-11-06 17:40 | The AWS credential check looks only at env vars and whether `~/.aws/credentials`/`config` exist; it does not call `aws sts get-caller-identity`. | That call would be an AWS action; the brief forbids AWS actions and reading credentials. |
| D5 | 2026-11-06 17:40 | `AWS_CONFIRM=yes` gates `aws-apply`, `aws-start`, `push-images-aws`, `push-images-aws-docker`, `e0-aws-node` (they create or start billed resources). `aws-plan` is not gated (it creates nothing in AWS) but still needs credentials. `aws-destroy` keeps its typed "destroy skypiea" prompt. | Safety without blocking review of the plan. |
| D6 | 2026-11-06 17:40 | Cluster, registry and port-forward ports are environment-overridable instead of fixed. Defaults are unchanged. | Lets tests and CI run on a separate cluster (`skypiea-rt`) without touching the running `skypiea` live stacks, and gives a fix when a port is in use. |
| D7 | 2026-11-06 17:40 | No `clean-models` target. | `models/` may hold a maintainer's only copy of bundles built from datasets; re-running `make fetch-models` replaces them. README says `rm -rf models/lgbm-*` for a manual clean. |
| D8 | 2026-11-06 17:41 | The release slug lives in `demo/fetch_models.sh` (`MODELS_REPO`, default `SYNKRONOX/skypiea-prewarm`), not in `configs/`. | `configs/` is outside this branch's ownership. A fork's clone still downloads from the original release unless `MODELS_REPO` is set, which is what a red-team clone needs. |
| D9 | 2026-11-06 17:41 | Checked (read-only HTTP HEAD) that `https://github.com/SYNKRONOX/skypiea-prewarm/releases/download/v1.2/SHA256SUMS` returns 404: the v1.2 release asset is NOT published. Publishing is forbidden here; README says so and W2_REPORT asks the user to publish it before 8 Nov. | Honesty: Tier 2 cannot work for a stranger until it is published. |
| D10 | 2026-11-06 17:46 | Training-data counts are copied by a script from the pipeline's split reports (git-ignored, local) into a committed, generated `docs/training_data.md`, with input hashes. | "Numbers read from the split files, not typed", and every number must sit in a committed file. |
