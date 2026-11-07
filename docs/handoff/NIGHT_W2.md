# NIGHT W2: post-merge round (branch `chore/post-merge`, from `origin/main` 66404ff)

**Nothing was pushed, merged, tagged or released.** No AWS or Terraform call was made. To
review: `git log --oneline origin/main..chore/post-merge`.

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

Literal guide run (TASK 8.5): fresh clone of `chore/post-merge` (7f1578b) into a path with a
space (`.../guide run/skypiea-prewarm`), guide section 2 version commands all answered
(Python 3.12.2, GNU Make 4.3, Docker 29.9.2, kind v0.33.0, kubectl v1.37.2, Helm v4.3.1).
- No Ollama (`AGENT_BASE_URL=http://127.0.0.1:9/v1 make doctor DOCTOR_MODE=live`): one
  `WARN no LLM ... (optional)` with the install line; `doctor: all required checks passed`.
- Docker not running (`DOCKER_HOST=unix:///nonexistent.sock make review`): Tier 0 PASS
  (setup 38 s, test 167 s, lint, sizes, check-claims), Tier 1 `doctor-demo ... FAIL`,
  `review: FAIL`, exit non-zero, 207 s; no cluster created (`kind get clusters`: only the other
  session's `skypiea`).
- Docker running (`make review`): every step PASS (test 172 s, demo 281 s, agent-demo 212 s,
  demo-failures 27 s, cli-status 1 s, clean-up 2 s), `review: PASS`, 698 s; afterwards only
  `skypiea` (the other session's) is left.
- `make report`: commit, OS, versions, doctor, test summary; `grep -c $HOME` = 0.
- `make fetch-models` (origin/main clone): three bundles in 2 s (S12).

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
| S12 | `make fetch-models` (2 s, three bundles) ended with "then: make live BUNDLES_DIR=models", though `BUNDLES_DIR` defaults to `models` and README says plain `make live` | `demo/fetch_models.sh` | fixed: "then: make live (it reads the bundles from models/)" |
| S11 | `make test-docker`, first version, mounted the checkout: in a git worktree `.git` is a file pointing outside the mount ("fatal: not a git repository") | `tools/test_docker.sh` | fixed: files are streamed in with `git ls-files` and committed into a throwaway repo inside the container |

`make test-docker` (TASK 8.4), this worktree, 2026-11-07: image `python:3.12-bookworm` pulled
(Python 3.12.16), `pip check` clean, sizes OK, ruff clean, check-claims 0 mismatches,
549 passed, 11 skipped, 1 xfailed; 231 s including the image pull. Shipped as `make test-docker`.

## Tasks: done and skipped

| Task | Status | Commit | Notes |
|---|---|---|---|
| 1 R1 include mk/console.mk; R5 links | done | c080b5f | `-include` became `include` (the file is on main); plain `make watch-pods` works. MEASURED_VS_SCRIPTED, SKYPIEA_WORLD, SUITE_BEFORE_AFTER linked from README and docs/README (LIVE_DEMO.md was already linked) |
| 2 README / dev.md refresh | done | 5ab821a | three scenarios + Auto-play wording, synthetic India-centred world (46 airports, INR, 11 Jan 2027), qwen models in the toggle, one `ollama pull` per model (S6), suite caveats wherever 54/54 is quoted (README Tier 2, Known limitations, dev.md, VIVA_QA, KNOWN_ISSUES). "100% reliable" appears nowhere. LLM licences checked with Ollama's local metadata and added to `docs/PORTABLE_MODELS.md`: llama3.1:8b Llama 3.1 Community License; qwen2.5:7b and qwen3:8b Apache 2.0 |
| 8 Reviewer guide (high priority, done right after 2) | done | bbdd094, 7f1578b | `docs/REVIEWER_GUIDE.md` (first line of README), `make review`, `make report` (in the issue template and CONTRIBUTING), `make test-docker` (passed locally), guide in check-claims' link/command/path checks. The guide is about 2 printed pages at small print (about 150 lines): sections a-f all needed their commands |
| 3 SECURITY / KNOWN_ISSUES: console on 127.0.0.1, no auth | done | 1de4c81, bbdd094 | bind verified in code (`console/app.py` `host="127.0.0.1"`, test) and at runtime (`ss -ltn`: :8090, :3001, :18180, :18280 on 127.0.0.1 only). The bind is in console code, so request R9 was appended (with the cross-site POST gap found while checking) |
| 4 check-claims | done | bbdd094 | `tools/check_claims.py`, `make check-claims`, `tests/test_check_claims.py`; in CI's test job (offline, deterministic, under 1 s). Mismatches it found in my files were fixed (S5); none found in Window 1's files except anchors that it now reads correctly |
| 5 Stranger test | done (Tier 2 `make live` skipped) | this file | origin/main fresh clone in a path with a space; Python 3.12 and 3.10 (pyenv shim and real binary); Docker not running simulated; no Ollama simulated. `make live` not run: the live ports and the `skypiea` cluster belong to another session running on this laptop |
| 6 TRIAGE / KNOWN_ISSUES | done | bbdd094 | labels are listed for a maintainer to create; none were created (outward-facing) |
| 7 VIVA_QA | done | 3703bb4 | 25 questions; each answer names its source file, or says no committed file backs it (OpenFaaS Pro, Knative/KEDA, AWS bill, why 20 sessions) |

## Assumptions (autonomous round: the most conservative option was taken)

- "Stop by 08:30 UTC" was read as 08:30 UTC on 8 Nov (the round started 11:00 UTC on 7 Nov).
- The brief's single `ollama pull a b c` line does not work (S6), so one model per line.
- Another session's kind cluster `skypiea`, registry `kind-registry` and two consoles were
  running. None was touched: every cluster run tonight used `skypiea-review` (registry
  :5002, ports 28080-28083 / 3300) and was deleted afterwards. Docker was never stopped;
  "Docker not running" was simulated with an unreachable `DOCKER_HOST`.
- The two untracked files in the worktree (`model_second_v1.0.txt`, `phase5,6_model.txt`)
  are not mine and were left alone (not committed).
- The gateway's missing `initialize` is by design (MCP 2026-07-28), so it is a low-priority
  request (R10), not an xfail test. The console's cross-site POST gap is a bug: xfail test.
- Commits were grouped per task where the files allowed; TASK 8 was inserted after TASK 2,
  so Tasks 3, 4, 6 and 8 share commits bbdd094 / 1de4c81.

## Verify

```bash
git log --oneline origin/main..chore/post-merge
make test lint check-sizes check-claims     # 585 passed, 1 xfailed; 0 mismatches
make review MODE=test                       # Tier 0 table, review: PASS
make review                                 # + Tier 1 on skypiea-review (about 14 min), deleted after
make report | grep -c "$HOME"               # 0: no home paths
pytest --runxfail tests/test_console_security.py   # shows R9 (expected to fail until fixed)
make help | grep -E "review|report|check-claims|test-docker|watch-pods"
```
