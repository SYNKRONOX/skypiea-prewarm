# Red-team triage table (issues #2-#27)

Branch `fix/redteam-2` (from `main` at `e29fb39`), plus `fix/redteam-1` (merged) for #7.
Teams: Altus = authors rutujmanware, vivek031, ancita13, SymmetricEntropy; Cloudhack = the
others (vaishu0702). Severity is taken from the issue body. Outcomes were checked on Tier 0:
for every code issue a regression test was written first and **failed before the fix** (the
named test file), then passed; the static issues (#11, #13) were checked by reading the policy.
Nothing in this branch was run on a cluster (CI runs Tier 1) and nothing was applied to AWS.
"Retest" is the reporting team's retest; it is `retest-requested` for every issue except #7.

| # | Team | Severity | Outcome | Evidence | Fix commit / status |
|---|---|---|---|---|---|
| 2 | Cloudhack | Minor | confirmed: `make doctor` (mode all) failed on a Tier 1 machine without Tier 2 bundles | `tests/test_doctor.py::test_all_mode_without_bundles_warns_not_fails` failed before the fix | `6899ecb`; retest-requested |
| 3 | Cloudhack | Minor | confirmed: all 18 images pinned the old base digest (apt 2.6.1) | `docker run` of the current upstream digest reports apt / libapt-pkg6.0 2.6.2; `tests/test_base_image_digest.py` | `3b6e2ac` (digest refreshed; images not rebuilt or trivy-scanned on this branch); retest-requested |
| 4 | Cloudhack | Minor | confirmed: a cold call blocked `cold_timeout_s` (120 s) although the controller could not scale | `tests/test_cold_fail_fast.py` failed before the fix | `0699e5c` (fail fast with -32004 after 2 failed scale-ups); retest-requested |
| 5 | Cloudhack | Minor | confirmed, related to #23 but **not the same root cause**: #23 is input validation inside `/v1/warm`; #5 is unauthenticated callers driving warm-pool cost (direct `/v1/warm` and the gateway's demand warms) | issue steps; `tests/test_controller_warm_input.py::test_warm_needs_the_admin_token` | `20a94bf` closes the direct unauthenticated `/v1/warm` route. **Not fixed: amplification through `POST /mcp` demand warms.** Cause: the stateless MCP gateway takes calls from any in-cluster client by design. Impact: bounded cost (caps `max_total` 20, 2 starts/node, TTL eviction). Workaround: opt-in NetworkPolicy / restrict who can reach the gateway. retest-requested |
| 6 | Cloudhack | Minor | confirmed: `to_csv` wrote `=...` cells verbatim | `tests/test_console_csv_injection.py` failed before the fix | `a9a7de3`; retest-requested |
| 7 | Cloudhack | Major | confirmed: unauthenticated session enumeration / cross-session access | `tests/test_gateway_session_auth.py`; `demo/session_auth_check.sh` | `e5de779` (+ `5ef2734`, `bb56ec4`, merged in `e29fb39`); **retest-passed** (Cloudhack comment on the issue, tested `e29fb39`) |
| 8 | Altus | Minor | confirmed: `skypiea status` read the stub's policy while the gateway served the model | code: `skypiea_cli/cli.py` read only `svc/skypiea-predictor`; `tests/test_cli_effective_policy.py` failed before the fix | `0589485`; retest-requested |
| 9 | Altus | Major | confirmed: "Yes, but please do not cancel my booking" counted as consent; `\bbook` matched "booking" | `tests/test_guard_negated_yes.py` failed before the fix | `45d7635`; retest-requested |
| 10 | Altus | Major | confirmed: confirmation compared only the tool name, not the quoted booking | `tests/test_guard_quote_target.py` failed before the fix | `1affef7`; retest-requested |
| 11 | Altus | Major | confirmed (static review): unconditioned `ec2:CreateTags` defeats the `project=skypiea` tag scoping | `infra/aws/operator-policy.json` at `e29fb39`; `tests/test_aws_policy_tags.py` failed before the fix | `f58b3db` (repo only; **the live AWS account was not changed**); retest-requested |
| 12 | Altus | Minor | confirmed: contract had no upper bound on `start_in_ms` / `ttl_s` / decisions | `tests/test_contract_bounds.py` (the gateway test hung on the 10^12 ms sleep before the fix) | `e684663`; retest-requested |
| 13 | Altus | Major | confirmed (static review): operator could write any inline policy on `skypiea-node` and use it via SSM | `infra/aws/operator-policy.json`, `infra/aws/main.tf` at `e29fb39`; `tests/test_aws_policy_boundary.py` failed before the fix | `df8b6c0` (repo only; **the live AWS account was not changed**; an admin must create `skypiea-node-boundary`); retest-requested |
| 14 | Altus | Major | confirmed: full history sent; predictor answered 413 after call 1,000 | `tests/test_gateway_history_window.py` failed before the fix | `c3f5c39` (P3: last 200 calls + `history_total`); retest-requested |
| 15 | Altus | Major | confirmed: `await request.body()` with no cap | `tests/test_gateway_body_limit.py` failed before the fix | `fc3e36f` (1 MiB, 413 before buffering); retest-requested |
| 16 | Altus | Major | confirmed: `RemoteProtocolError` after sending was retried | `tests/test_gateway_no_replay.py` failed before the fix | `220fa18` (-32003, never re-sent); retest-requested |
| 17 | Altus | Minor | confirmed: `seats_left` never decremented, passengers not compared with seats | `tests/test_skyair_issue17.py` failed before the fix | `bedd0a2`; retest-requested |
| 18 | Altus | Major | confirmed (code): one 200-connection pool shared by tool calls, predictor and controller (`gateway/main.py`) | `tests/test_gateway_pools.py` failed before the fix (the starvation load test itself was not re-run) | `7733ebc`; retest-requested |
| 19 | Altus | Major | confirmed: a declined quote never expired | `tests/test_guard_quote_expiry.py` failed before the fix | `4a56dba`; retest-requested |
| 20 | Altus | Major | confirmed: no timeout on the scale patch; `/healthz` ignored the loop; no livenessProbe | `tests/test_controller_timeouts.py` failed before the fix | `db1ad7e`; retest-requested |
| 21 | Cloudhack | Minor | confirmed: same-flight change charged the fee | `tests/test_skyair_issue21.py` failed before the fix | `eb9da86`; retest-requested |
| 22 | Cloudhack | Minor | confirmed: percentage of `paid` (incl. bags) | `tests/test_skyair_issue22.py` failed before the fix | `4b32a0d`; retest-requested |
| 23 | Altus | Major | confirmed: raw `reason` as a metric label, `refused` list never cleared, unbounded numbers -> 500 | `tests/test_controller_warm_input.py` failed before the fix | `20a94bf`; retest-requested |
| 24 | Cloudhack | Minor | confirmed: tracebacks for a wrong `--context` / missing config | `tests/test_clean_errors.py` failed before the fix | `b5c4f0c`; retest-requested |
| 25 | Altus | Major | confirmed: text-written call gated by `offered_tools()`, not the turn's `tools=[]` | `tests/test_guard_text_call_suppressed.py` failed before the fix | `5582f9d`; retest-requested |
| 26 | Altus | Major | confirmed: no cold/warm-labelled histogram (FR-5 acceptance text) | `tests/test_fr5_cold_warm_histogram.py` failed before the fix | `9e9e510` (histogram + Grafana panel; not yet seen on a cluster); retest-requested |
| 27 | Altus | Major | confirmed: unauthenticated `POST /admin/policy`, `/decide` 500 on a bad config, unbounded `seen` | `tests/test_predictor_stub_admin.py` failed before the fix | `cd8a4d9`; retest-requested |

## Not fixed

- **#5 (partly).** Cause: the gateway's `POST /mcp` accepts calls from any in-cluster client
  (stateless MCP, no client auth), and each cold call asks for a demand warm. Impact: an
  in-cluster caller can still add bounded idle cost (caps `max_total` 20, 2 starts per node,
  TTL eviction). Workaround: enable the opt-in NetworkPolicy and keep the gateway ClusterIP;
  the direct `/v1/warm` route now needs the admin token.

## What was not verified on this branch

- No Tier 1 / cluster run (`make demo`, `make live`, `make review`): the admin-token wiring for
  the stub (#27) and the controller (#23) is covered by unit and chart tests only. If `make
  demo` / `make live` break on the token, the instructions are to revert only `cd8a4d9` (#27);
  #23's token change would then need the same check.
- No AWS action: #11 / #13 are repository changes only.
- No image rebuild or trivy rescan for #3.
