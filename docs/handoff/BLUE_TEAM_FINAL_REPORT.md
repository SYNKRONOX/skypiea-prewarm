# Blue-team final report: Skypiea (predictive prewarming for MCP tool servers)

## 1. Project and team

| | |
|---|---|
| Repository | `SYNKRONOX/skypiea-prewarm` |
| Team members | G. Parthav, L. Mohnish, Ch. Dhanunjaya |
| Baseline tag and commit | `rt-baseline` `8f81eb7` |
| Final commit | `<H>` (branch `fix/redteam-2`, from `main` `e29fb39`; not merged at the time of writing) |
| Deployment/test environment | Laptop, WSL2 (Linux 6.18), Docker 29.9.2, kind cluster (1 control plane + 1 worker) with a local registry, Helm chart `charts/skypiea`; Live Console with both stacks (`skypiea-base`, `skypiea-pred`); local LLM llama3.1:8b through Ollama (RTX 5070 Laptop GPU). AWS (EKS) account: **not changed** by this round. GitHub Actions CI (Tier 0 + kind e2e) runs on push. |

Red teams: Altus (rutujmanware, vivek031, ancita13, SymmetricEntropy) and Cloudhack (Red Team 2).
One row per issue with evidence: [`TRIAGE_TABLE.md`](TRIAGE_TABLE.md).

## 2. Summary

| | Count |
|---|---:|
| Issues received | 26 (#2-#27; Altus 17, Cloudhack 9) |
| Confirmed Major | 15 |
| Confirmed Minor | 11 |
| Fixed and retested | 1 (#7, retest-passed by Cloudhack on `e29fb39`) |
| Fixed but awaiting retest | 24 (every other issue except #5) |
| Open or partially fixed | 1 (#5, partially fixed) |
| Duplicate / invalid / cannot-reproduce / works-as-designed | 0 |
| Regression tests added | 23 new test files (73 tests) plus new cases in `test_doctor.py`, `test_guard_negated_yes.py`, `test_session_auth_check.py`, `test_chart_security.py`, `test_controller.py`; every code fix had its test written first and failing before the fix, except #3 (image digest) |

## 3. Issue resolution

Fix/PR: commit on `fix/redteam-2` (hashes may change on merge; the issue comments carry the
final ones). Red-team result: only what the reporting team posted on the issue.

| Issue | Severity | Classification | Fix/PR | Regression test | Red-team result | Final status |
|---|---|---|---|---|---|---|
| #2 `make doctor` fails on a Tier 1 machine | Minor | confirmed | `6899ecb` | `test_doctor.py::test_all_mode_without_bundles_warns_not_fails` | retest-requested | fixed, awaiting retest |
| #3 outdated base image digest | Minor | confirmed | `3b6e2ac` | `test_base_image_digest.py` | retest-requested | fixed, awaiting retest |
| #4 120 s block when the controller cannot scale | Minor | confirmed | `0699e5c` | `test_cold_fail_fast.py` | retest-requested | fixed, awaiting retest |
| #5 warm-pool cost amplification | Minor | confirmed (related to #23, different root cause) | `20a94bf` (partial) | `test_controller_warm_input.py::test_warm_needs_the_admin_token` | retest-requested | **partially fixed** (section 6) |
| #6 CSV formula injection | Minor | confirmed | `a9a7de3` | `test_console_csv_injection.py` | retest-requested | fixed, awaiting retest |
| #7 session enumeration | Major | confirmed | `e5de779` (+ `5ef2734`, `bb56ec4`; merged `e29fb39`) | `test_gateway_session_auth.py`, `demo/session_auth_check.sh` | **retest-passed** | fixed and retested |
| #8 CLI reports the wrong policy | Minor | confirmed | `0589485` | `test_cli_effective_policy.py` | retest-requested | fixed, awaiting retest |
| #9 negated "yes" treated as consent | Major | confirmed | `45d7635`, `1a1f657` | `test_guard_negated_yes.py` | retest-requested | fixed, awaiting retest |
| #10 confirmation not tied to the quoted booking | Major | confirmed | `1affef7` | `test_guard_quote_target.py` | retest-requested | fixed, awaiting retest |
| #11 unrestricted `ec2:CreateTags` | Major | confirmed (static review) | `f58b3db` (repo only) | `test_aws_policy_tags.py` | retest-requested | fixed in repo, awaiting retest; AWS not changed |
| #12 `start_in_ms` unbounded | Minor | confirmed | `e684663` | `test_contract_bounds.py` | retest-requested | fixed, awaiting retest |
| #13 operator escalation via the node role | Major | confirmed (static review) | `df8b6c0` (repo only) | `test_aws_policy_boundary.py` | retest-requested | fixed in repo, awaiting retest; AWS not changed |
| #14 prewarming stops after call 1,000 | Major | confirmed | `c3f5c39` | `test_gateway_history_window.py` | retest-requested | fixed, awaiting retest |
| #15 no request-size limit on `/mcp` | Major | confirmed | `fc3e36f` | `test_gateway_body_limit.py` | retest-requested | fixed, awaiting retest |
| #16 duplicate `tools/call` after a drop | Major | confirmed | `220fa18` | `test_gateway_no_replay.py` | retest-requested | fixed, awaiting retest |
| #17 `seats_left` never decremented | Minor | confirmed | `bedd0a2` | `test_skyair_issue17.py` | retest-requested | fixed, awaiting retest |
| #18 one shared connection pool | Major | confirmed | `7733ebc` | `test_gateway_pools.py` | retest-requested | fixed, awaiting retest |
| #19 stale quote never expires | Major | confirmed | `4a56dba` | `test_guard_quote_expiry.py` | retest-requested | fixed, awaiting retest |
| #20 hung Kubernetes call freezes scaling | Major | confirmed | `db1ad7e` | `test_controller_timeouts.py` | retest-requested | fixed, awaiting retest |
| #21 fee for a same-flight change | Minor | confirmed | `eb9da86` | `test_skyair_issue21.py` | retest-requested | fixed, awaiting retest |
| #22 promo discounts baggage too | Minor | confirmed | `4b32a0d` | `test_skyair_issue22.py` | retest-requested | fixed, awaiting retest |
| #23 `/v1/warm` input validation | Major | confirmed | `20a94bf`, `490b746` | `test_controller_warm_input.py`, `test_session_auth_check.py` | retest-requested | fixed, awaiting retest |
| #24 tracebacks on bad context/config | Minor | confirmed | `b5c4f0c` | `test_clean_errors.py` | retest-requested | fixed, awaiting retest |
| #25 text-written tool call bypasses the guard | Major | confirmed | `5582f9d` | `test_guard_text_call_suppressed.py` | retest-requested | fixed, awaiting retest |
| #26 no cold/warm latency histogram (FR-5) | Major | confirmed | `9e9e510` | `test_fr5_cold_warm_histogram.py` | retest-requested | fixed, awaiting retest |
| #27 stub predictor admin endpoint | Major | confirmed | `cd8a4d9`, `490b746` | `test_predictor_stub_admin.py`, `test_session_auth_check.py` | retest-requested | fixed, awaiting retest |

## 4. Key fixes

- **One admin token for every mutating endpoint** (#7, #23, #27): session introspection,
  the stub predictor's policy endpoint and the controller's warm endpoint all need the
  per-install Secret `skypiea-admin`; it is mounted only into the gateway, the controller and
  the stub predictor (checked on the cluster by `demo/session_auth_check.sh`). Every in-repo
  caller sends it: CLI, Live Console, demo scripts, scenario/replay/concurrent checks, agent.
- **Bounded inputs everywhere** (#12, #15, #23, #27): request body limit, contract bounds on
  prewarm decisions, validated controller and stub inputs, fixed metric label sets, capped
  per-session state.
- **Gateway robustness** (#4, #14, #16, #18): sliding 200-call history with a total (P3),
  no replay of a call that may have reached the tool, separate connection pools per plane,
  fail fast when the controller cannot scale.
- **Controller liveness** (#20): timeouts on every Kubernetes call, a stuck loop reported by
  `/healthz`, livenessProbe.
- **Agent guardrails** (#9, #10, #19, #25): consent must be an unnegated yes, for the quoted
  target, on the very next turn; tool calls written as text obey the turn's tool suppression.
  The scenario suite found one regression of the #9 change ("I confirm the booking" no longer
  confirmed a new booking); fixed in `1a1f657` with a test (section 7).
- **Mock airline correctness** (#17, #21, #22) and **operability** (#2, #3, #6, #8, #24, #26).

## 5. Security remediation

- **Authentication.** All admin and mutating HTTP endpoints of the platform require the
  shared admin token (constant-time comparison; no token configured means the endpoint is
  closed). Verified on both live stacks: no token and a wrong token are refused with 401; the
  token is never printed or logged by the checks.
- **Resource exhaustion.** Inputs that could exhaust memory, tasks or metric cardinality are
  bounded and answered with 4xx instead of 5xx.
- **Unsafe agent actions.** Writes need fresh, unambiguous, target-matching consent.
- **Export safety.** Spreadsheet formula cells in console exports are neutralised.
- **Supply chain.** Base image digest refreshed (images rebuilt by `make demo` / `make live`;
  a fresh vulnerability rescan by the red team is requested).
- **AWS IAM (#11, #13): repository changes only. The live AWS account was not changed.**
  Tag creation is limited to resource creation with the project tag; the node role can only
  be written under a permissions boundary the operator cannot remove. **#13 needs an account
  administrator to create the `skypiea-node-boundary` managed policy**
  (`infra/aws/node-boundary-policy.json`) and re-attach the updated operator policy before
  the fix takes effect.

## 6. Unresolved issues

**#5 (partially fixed).**
- Cause: the stateless MCP gateway accepts `POST /mcp` from any client inside the cluster,
  and every cold call asks the controller for a demand warm.
- Fixed part: the controller's warm endpoint no longer accepts unauthenticated requests.
- Impact of the remaining part: an in-cluster client can still add idle pod cost through
  cold `/mcp` calls; bounded by the controller caps (`max_total` 20, 2 starts per node, TTL
  eviction).
- Workaround: enable the opt-in NetworkPolicy (`networkPolicy.enabled`) and keep the gateway
  ClusterIP-only.
- Next step: per-client authentication or a per-client rate limit on demand warms.

**Pending verification (not open issues):** #11 and #13 take effect only once applied to the
AWS account by an administrator; the red team's own load reproductions (#15, #18) and an image
rescan (#3) were not re-run by us.

## 7. Evidence

- **Commits:** `fix/redteam-2` from `e29fb39` (one commit per issue, messages "Refs #N",
  never "Fixes"); list with `git log --oneline e29fb39..<H>`. Verification commits:
  `05c0665` (CI flake, below), `490b746` (session-auth check for the new token holders),
  `1a1f657` (#9 regression found by the scenario suite).
- **CI run:** last `main` run [44114708971](https://github.com/SYNKRONOX/skypiea-prewarm/actions/runs/44114708971)
  (`e29fb39`): e2e passed, test failed on one red-team determinism test that a slow runner's
  flagged time-budget fallback broke; fixed in `05c0665`. `fix/redteam-2` is not pushed, so
  it has no CI run yet: the first push produces it.
- **Local Tier 0 + Tier 1 (`make review`) on `490b746`:** PASS on every step (doctor, setup,
  773-test suite, ruff, check-sizes, check-claims; kind demo, agent demo, failure injection,
  CLI status, session-auth), 669 s. The first run on `1a6bbfd` failed only `session-auth`
  (the check still expected the gateway to be the Secret's only user); fixed in `490b746`.
- **Live stacks (`make live`):** on both stacks, `POST /admin/policy` and `POST /v1/warm`
  answer 401 without a token and with a wrong token, 200 with it; console Normal policy
  switch (keep-alive 30 s and back), Kill predictor (0 ready) and Restart predictor (1 ready,
  `/healthz` ok); `skypiea status` on both namespaces (the predictive one reports the real
  predictor and `policy set` refuses there); two full console runs (Skypiea Air, hint off and
  typing hint), every call ok on both stacks. Demo runs, not results.
- **Scenario correctness suite** (llama3.1:8b, 1 rep, no cluster):
  `results/suite/redteam2_check/` 16/18 before the #9 regression fix (both failures
  `new_booking`), `results/suite/redteam2_final/` **18/18** after it; 18/18 at
  the earlier final check (`results/suite/final_check/`).
- **Tests:** `make test`; the regression test per issue is named in section 3.
- **Retest comments:** #7: Cloudhack (vaishu0702) posted **retest-passed** on 2026-11-18
  against `e29fb39`. Every other issue: retest requested, no result yet.

LLM assistance: Claude (Anthropic) in Claude Code read the issues, wrote the failing tests,
the fixes and these documents, and ran the checks above; the team reviews the diffs.
