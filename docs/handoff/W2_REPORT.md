# W2 report: red-team readiness (branch `feat/redteam-ready`)

Autonomous session, 2026-11-06 (system clock, UTC; IST = UTC + 5:30), branched from `main`
(da67ce7). Nothing was pushed, merged, tagged or published. No AWS action was taken and no
credential was read. No package was added (the lock is unchanged). Every change is listed in
[`CHANGES_W2.md`](CHANGES_W2.md), every choice in [`DECISIONS_W2.md`](DECISIONS_W2.md), the
self red-team in [`SELF_REDTEAM.md`](SELF_REDTEAM.md), and problems in files this branch does
not own in [`REQUESTS_W2.md`](REQUESTS_W2.md).

## Do these before 8 Nov (the user; this session must not)

1. **Publish GitHub Release v1.2 with the model archive.** On 2026-11-06,
   `https://github.com/SYNKRONOX/skypiea-prewarm/releases/download/v1.2/SHA256SUMS` returned
   404, so README Tier 2 (`make fetch-models`, `make live`) cannot work from a fresh clone. The
   archive is built by `make models-archive` (`dist/skypiea-models-af000231.tar.gz` and
   `dist/SHA256SUMS`; see `docs/PORTABLE_MODELS.md`). After publishing, check with
   `FETCH_DRY_RUN=1 make fetch-models` (prints the URL) and `make fetch-models` in a fresh
   clone, then delete the "not published" sentences in README (Tier 2 and Known limitations)
   and CHANGELOG v1.2.
2. **Push the branch or merge it, and watch CI.** The new `e2e` job has never run on GitHub
   (see "CI status").
3. Enable private vulnerability reporting (repository Settings > Code security) or change
   `SECURITY.md`, which tells reporters to use it if it is enabled.
4. After merging `feat/demo-polish`: apply its request R2 to `docs/PRD_STATUS.md` (E5 row),
   pointing at `results/fr6_reload_check.json`; it is not done here because that file is not on
   this branch (DECISIONS_W2 D16).

## What changed (by brief item)

| # | Item | Result | Commits |
|---|---|---|---|
| 1 | `make doctor` | `tools/doctor.sh test\|demo\|live\|all`: OS/WSL, Docker running + memory (6 GB) , kind/kubectl/helm with versions, Python 3.12 (honours `PYTHON`), curl/sha256sum/tar, disk (15 GB), RAM, registry and demo/live ports, bundles, optional LLM. Each FAIL prints `-> fix:`. `make demo` and `make live` run it quietly first and stop before changing anything; `make setup` says clearly when Python 3.12 is missing. 12 tests with a fake PATH | 5277cd3, f124608, f9d7d25 |
| 2 | Portability | No user path in Makefile, mk/, demo/*.sh, tools/, infra/, .github/. Others' files: REQUESTS R1-R4. Platforms stated in README: WSL2 and Linux x86-64 tested, macOS/arm64 untested | a6b2af5, 86f7537 |
| 3 | Targets: fresh clone, idempotent, clean-up, AWS refusal | Every README/dev.md `make X` is a real target (checked); `make help` is the default goal; `make clean-images` added; clean-up column in README "Make targets". AWS: `infra/aws/scripts/aws-guard.sh` in all 17 AWS targets: no aws/terraform call without CLIs, credentials (env or the existence of `~/.aws/*`, contents not read) and local state; `aws-apply`, `aws-start`, `push-images-aws(-docker)`, `e0-aws-node` also need `AWS_CONFIRM=yes`. 26 tests with fake binaries; a test fails if a new AWS target lacks the guard. Cluster, registry and ports are environment-overridable | c8cbb9f, 86f7537 |
| 4 | `make fetch-models` | URL from `MODELS_REPO` (default `SYNKRONOX/skypiea-prewarm`); `MODELS_REPO=""` uses origin only if it is GitHub (ssh/https), else refuses; slug checked; `FETCH_DRY_RUN=1`; "not available ... Nothing was changed" (exit 2). Tests for each rule | cbddd34 |
| 5 | CI end to end | `.github/workflows/ci.yml` job `e2e`: clean `ubuntu-latest`, frees disk, pinned kind v0.33.0 / kubectl v1.37.2 / Helm v4.3.1 (cached), pip cache, `make setup`, `make doctor`, `demo/ci_e2e.sh` (= `make demo` + `make agent-demo LLM=scripted` + all tool Deployments back to 0 within 300 s), logs as an artifact. `test` job kept (+ doctor) | 73fc42a, 7c3813b, f9d7d25 |
| 6 | Security and hygiene | `make secret-sweep HISTORY=1`: tree clean, 180 commits clean, every ECR host in data files masked; LICENSE (MIT) present; dataset licences `data/README.md`; bundle attribution `docs/MODELS_ATTRIBUTION.md`. Vulnerable pins: **not checkable offline** (no advisory DB or pip-audit; none added), stated in SECURITY.md | 7c3813b |
| 7 | Self red-team | 16 checks; 7 problems fixed in this branch's files, 4 requests (R5-R8). See SELF_REDTEAM.md | f124608 .. ba5383c |
| 8 | README | Install-and-use guide in tiers 0-3 (prerequisites, commands, expected output, time with sources), platforms, what Skypiea is, architecture, what runs where, training data, Known limitations (prominent, linked at the top), results (unchanged content), troubleshooting, FAQ, make targets with clean-up, team, links | 86f7537 |
| 9 | Training data table | `tools/training_data_table.py` copies the split tables from `data/processed/<source>/report.md` into `docs/training_data.md` and marked blocks in README and dev.md, with input hashes; `--check` catches drift | b333ffa, d764074 |
| 10 | dev.md, PRD_STATUS, TESTING | dev.md checked against the code (all matched) and extended; PRD_STATUS paths/tests all exist, numbers spot-checked, links TESTING.md; `docs/TESTING.md` explains every check | d764074, 55f5333, 86f7537 |
| 11 | Consistency pass | Links, anchors, make targets, paths and sample numbers checked across the nine docs; stale v1.1/"in progress" text fixed | 55f5333 |
| 12 | Issue template, CONTRIBUTING, SECURITY, CHANGELOG | Bug form (environment = `make doctor`, steps, expected, actual), CONTRIBUTING, SECURITY, CHANGELOG v1.2 entry | 296b74e |

Other fixes found on the way: `tools/build_images.sh` needed `bc` and a system PyYAML; the image
tag changed whenever a helper script in `tools/` changed (now only image inputs count);
`make demo-portforward` reported success when another folder held its ports; `make agent-demo`
printed a Python traceback when the cluster stopped; `make live-clean` claimed to remove stacks
that were not there; `make` with no target started building all images.

## CI status

- **Not run on GitHub** (no push allowed). The workflow parses as YAML; `actionlint` is not
  installed here.
- The e2e logic was run locally with `demo/ci_e2e.sh` on a separate kind cluster
  (`CLUSTER=skypiea-rt`, registry :5002, ports 28080+/3300): **PASS, 448 s**
  ([`docs/TESTING.md`](../TESTING.md#measured)), and again on the final code (see the last row
  of the table below).
- Risks on a hosted runner that could not be tested here: free disk after the clean-up step
  (doctor's threshold is lowered to 10 GB there), the download URLs of the pinned binaries, and
  build time without a Docker layer cache (the job has a 60 min timeout).

| Final checks on this branch | Result |
|---|---|
| `make test` | 472 passed, 6 skipped, 0 failed (148 s) |
| `make lint` | `All checks passed!` |
| `make check-sizes` | 664 tracked files, none over 50 MB |
| `make secret-sweep HISTORY=1` | clean (tree; 205 commits) |
| `demo/ci_e2e.sh` on `skypiea-rt` (final code) | PASS in 282 s (doctor, `make demo`, scripted agent, all 16 tools back to 0) |

## Known limitations and their README entries

All are in README "Known limitations" (and the full list in `docs/limits.md`):

| Limitation | README entry |
|---|---|
| Release v1.2 asset not published: Tier 2 cannot run from a fresh clone | "Tier 2 needs the v1.2 release asset" (also Tier 2 "The bundles", Troubleshooting) |
| Console needs an LLM; no scripted console mode (R6) | "The Live Console needs an LLM" |
| Console's LLM can claim actions it did not take | "The Live Console's LLM can claim actions ..." |
| macOS / arm64 untested | "Only Linux x86-64 and Windows + WSL2 are tested" (also "Supported platforms") |
| `make live` ports fixed (R7) | "`make live` uses fixed local ports" |
| `make demo-clean` deletes the whole cluster, live stacks included | "`make demo-clean` and `make clean-cluster` delete the whole kind cluster" |
| CI builds images without a layer cache | "CI does not cache Docker layers" |
| Result limits (small AWS runs, one node, simulator assumptions, profile adopted after TEST, small tau2 with simulated users, synthetic prd, kind = demo runs, no independent review) | the eight result items, unchanged in substance |

## Requests for the other session

[`REQUESTS_W2.md`](REQUESTS_W2.md): R1 `docs/DEMO.md` says macOS should work; R2
`sim/lookahead.py` docstring user path; R3 `docs/aws_plan.md` names `~/skypiea-infra` (no owner);
R4 `results/sim_lookahead_*.json` record an absolute path (provenance, leave or fix on re-run);
R5 `agent/` traceback on connection errors; R6 console scripted-LLM mode; R7 console ports from
the environment; R8 `deploy/kind/down.sh` message. Their own requests to this branch (`REQUESTS_W1.md` on `feat/demo-polish`): R1
done as `-include mk/console.mk` (D15); R2 after the merge (above); R3 is outside both lists.

## Safety record

- One read-only `kubectl get svc` reached the live cluster's empty `skypiea-tools` namespace
  when a command ran without the test variables (D13). Nothing was created or changed; the
  live stacks (`skypiea-base`, `skypiea-pred`) were never touched. Docker was never stopped
  (D14).
- `make aws-plan` was never run (it would call AWS with the machine's credentials). AWS
  refusal was tested with fake binaries and with `make aws-apply` without `AWS_CONFIRM`.
- The test cluster `skypiea-rt` and its registry `kind-registry-rt` were deleted at the end
  (`make demo-clean` twice: idempotent).

## Merge commands (for the user)

```bash
git checkout main
git merge --no-ff feat/redteam-ready      # this branch
make test lint check-sizes
# then the other session's branch; expected overlap: docs/PRD_STATUS.md (they edit only the
# FR-6/FR-9 rows, this branch only the header line), and mk/console.mk is picked up by the
# optional include. If their mk/console.mk later moves the Live Console targets out of
# mk/demo.mk, delete them from mk/demo.mk in the same commit (make warns about overriding recipes).
git merge --no-ff feat/demo-polish
make help | grep -E 'fr6-reload|hold-report'   # the console targets are visible
make test lint check-sizes
```
