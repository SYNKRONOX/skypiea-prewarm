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

## Snags found (stranger test, TASK 5 and TASK 8.5) and what was done

| # | Snag | Where | Fixed? |
|---|---|---|---|
| S1 | `make test` takes about 4 min on a fresh clone (250 s); README Tier 0 said "about 2 min" | README | fixed: README cites this file (59 s setup, 250 s test) |
| S2 | `make doctor PYTHON=python3.10` where `python3.10` is a pyenv shim of an inactive version printed "python3.10 is Python , not 3.12" (empty version) plus a second, misleading FAIL "python3.12 has no venv/ensurepip" | `tools/doctor.sh` | fixed: "is on PATH but does not run (a pyenv shim ...)" with the pyenv path as the fix; the ensurepip check is skipped when the interpreter does not run |
| S3 | `make setup PYTHON=<python3.10>` created `.venv` with 3.10 and failed deep in pip ("No matching distribution found for contourpy==1.4.0"); the broken 3.10 `.venv` stayed, so a later `make setup` with 3.12 reused it and failed the same way, and `make doctor` said "OK ... next: make setup" | `Makefile` setup, `tools/doctor.sh` | fixed: setup checks the interpreter is 3.12 before creating `.venv` and refuses a non-3.12 `.venv`; doctor FAILs on a non-3.12 `.venv` with "delete the .venv folder" |
| S4 | Docker not running (simulated with `DOCKER_HOST=unix:///nonexistent.sock`, because other sessions use the real daemon): `make doctor DOCTOR_MODE=demo` said `FAIL Docker is installed but the daemon is not reachable` with one `-> fix:` line | `tools/doctor.sh` | already fine |
| S5 | `docs/README.md` linked `handoff/phase2_model.txt`, which is git-ignored (`docs/handoff/*.txt`), so the link is dead in every clone | `docs/README.md` | fixed (line removed); found by `make check-claims` |
| S6 | The brief's `ollama pull llama3.1:8b qwen2.5:7b qwen3:8b` fails: `ollama pull` accepts exactly one model ("Error: accepts 1 arg(s), received 2", Ollama on Windows) | README, dev.md | written as one `ollama pull` per model; noted in KNOWN_ISSUES and REQUESTS_W2 R12 |
| S7 | A literal `make demo` on a machine that already runs `make live` (or another checkout's demo) reuses the `skypiea` cluster: on this laptop that is another session's live cluster | REVIEWER_GUIDE, `make review` | `make review` uses its own cluster `skypiea-review` and refuses if it exists; the guide says how to pick another cluster for `make demo` |
| S8 | The gateway does not answer MCP `initialize` (-32601); it implements the stateless revision 2026-07-28. Standard MCP inspectors cannot connect | `gateway/` (Window 1) | documented in the guide and KNOWN_ISSUES; REQUESTS_W2 R10 |
| S9 | The Live Console accepts cross-site POSTs (no Origin / Content-Type check): a web page in the same browser can kill the predictor or change the hint | `console/` (Window 1) | SECURITY.md and KNOWN_ISSUES; xfail test; REQUESTS_W2 R9 |
| S10 | `docs/LIVE_DEMO.md` still says "Until the v1.2 release exists" and `make -f Makefile -f mk/console.mk watch-pods` | Window 1's file | REQUESTS_W2 R11 |
| S11 | `make test-docker`, first version, mounted the checkout: in a git worktree `.git` is a file pointing outside the mount ("fatal: not a git repository") | `tools/test_docker.sh` | fixed: files are streamed in with `git ls-files` and committed into a throwaway repo inside the container |

`make test-docker` (TASK 8.4), this worktree, 2026-11-07: image `python:3.12-bookworm` pulled
(Python 3.12.16), `pip check` clean, sizes OK, ruff clean, check-claims 0 mismatches,
549 passed, 11 skipped, 1 xfailed; 231 s including the image pull. Shipped as `make test-docker`.
