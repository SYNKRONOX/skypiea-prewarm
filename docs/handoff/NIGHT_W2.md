# NIGHT W2: post-merge round (branch `chore/post-merge`, from `origin/main` 66404ff)

Autonomous round, 2026-11-07 from 11:00 UTC. Rules: no push/merge/tag/release, no AWS or
Terraform, no secrets, no datasets, numbers only from committed files, PRD.md and Window 1's
files untouched (problems there go to [`REQUESTS_W2.md`](REQUESTS_W2.md) R9-R12).

## Measurements recorded tonight (the source for the timings in `docs/REVIEWER_GUIDE.md`)

All on the tested laptop (Windows 11 + WSL2, Ubuntu 24.04, x86-64, 24 CPUs, Docker 29.9.2 with
16 GB), 2026-11-07.

| Run | Step | Result | Time |
|---|---|---|---|
| Fresh clone of origin/main 66404ff in a path with a space (`.../stranger dir/skypiea-prewarm`) | `make doctor DOCTOR_MODE=test` | pass | 1 s |
| same | `make setup` (new .venv) | pass | 59 s |
| same | `make test` | 546 passed, 3 skipped | 250 s |
| same | `make lint`, `make check-sizes` | pass | 1 s |
| `KEEP=1 make review` in this worktree (own cluster `skypiea-review`) | doctor-test, setup (venv existed), test, lint, check-sizes | PASS | 0 s, 16 s, 264 s, 0 s, 0 s |
| same | doctor-demo, demo, agent-demo, demo-failures, cli-status | PASS | 1 s, 295 s, 214 s, 27 s, 1 s |
| same | total | `review: PASS` | 818 s |

"Use Skypiea yourself" (REVIEWER_GUIDE section 3), run against the `skypiea-review` cluster
(`PF_BASE=28080`): `tools/list` listed 53 tool names; `initialize` answered -32601; a cold
`read_file` call had `x-skypiea-cold: 1`, `x-skypiea-added-ms: 2288.9`; the next `run_tests`
call in the same session had `x-skypiea-cold: 0`, `x-skypiea-predicted: 1`,
`x-skypiea-added-ms: 0.1`; a cold `user_lookup` call had `x-skypiea-held-ms: 150.0`;
`skypiea explain` said "ranked run_tests 1.00 ... HIT: it was predicted and found a warm pod"
and the warm decision had TTL 82 s; `POST /decide` on the model answered `run_tests` p 0.998416,
warm 1 copy, `latency_ms` 0.814. `skypiea status`, `sessions`, `trace`, `policy get` and
`kubectl get pods -n skypiea-tools --watch` worked. The cluster and its registry were then
deleted with `CLUSTER=skypiea-review REG_NAME=kind-registry-review make demo-clean`; the other
session's `skypiea` cluster and `kind-registry` were not touched.
