# Requests to the other session (files this branch does not own)

The other session owns `agent/`, `console/`, `gateway/`, `services/predictor/`, `mk/console.mk`,
`docs/LIVE_DEMO.md`, `docs/DEMO.md`, the demo section of `docs/VIVA.md` and the FR-6/FR-9 rows
of `docs/PRD_STATUS.md`. Problems found there are listed here; nothing in them was edited.

| # | File | Problem | Suggested fix |
|---|---|---|---|
| R1 | `docs/DEMO.md` line 25 (Prerequisites, OS row) | Says "any Linux or macOS with Docker should work". Only Linux x86-64 and Windows + WSL2 were tested; the scripts use GNU `sha256sum`/`ss`/`setsid`. | "Tested: Ubuntu 24.04 on WSL2 and Linux x86-64. macOS and arm64 are untested." (matches README "Supported platforms" and `make doctor`). |
| R2 | `sim/lookahead.py` line 3 (docstring) | Example command uses a user path `~/skypiea-prewarm/data/processed`. | `--data data/processed`. |
| R3 | `docs/aws_plan.md` lines 395-399 (no owner in either list) | Names a personal checkout `~/skypiea-infra` as the place the Terraform state lives. | "the checkout you ran `make aws-apply` from (`infra/aws/terraform.tfstate`)". |
| R4 | `results/sim_lookahead_{val,test}.json` field `data_root` | Records an absolute laptop path. It is run provenance, so this branch leaves result files unchanged. | Leave as is (provenance) or write a repo-relative path on the next re-run. |
