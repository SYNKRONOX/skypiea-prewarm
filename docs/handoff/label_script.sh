#!/usr/bin/env bash
# Labels and comments for red-team issues #2-#27 after branch fix/redteam-2.
# NOT RUN. Review, replace <H> with each fix commit as it lands on main, then run by hand.
# Issues stay OPEN: close only after the reporting team posts retest-passed.
# Fix commits on fix/redteam-2 (may change on rebase): see docs/handoff/TRIAGE_TABLE.md.
set -euo pipefail
R=SYNKRONOX/skypiea-prewarm

# #7: already fixed on fix/redteam-1 (merged); Cloudhack posted retest-passed.
gh issue edit 7 --repo "$R" --add-label "confirmed,major,fixed,retest-passed" --remove-label "retest-requested"
gh issue comment 7 --repo "$R" --body 'Thank you for the retest (retest-passed on e29fb39, fix e5de779). Labelled retest-passed.'

gh issue edit 2 --repo "$R" --add-label "confirmed,minor,retest-requested,fixed"
gh issue comment 2 --repo "$R" --body 'Fix commit <H> (branch fix/redteam-2). make doctor (mode all) now WARNs instead of failing when the Tier 2 bundles are missing; DOCTOR_MODE=live still fails with the make fetch-models fix. Test: tests/test_doctor.py::test_all_mode_without_bundles_warns_not_fails. Retest requested. The issue stays open until your retest passes.'

gh issue edit 3 --repo "$R" --add-label "confirmed,minor,retest-requested,fixed"
gh issue comment 3 --repo "$R" --body 'Fix commit <H> (branch fix/redteam-2). All 18 images now pin the current python:3.12-slim-bookworm digest (apt / libapt-pkg6.0 2.6.2). Images were not rebuilt or trivy-scanned on the fix branch; please rescan after make demo. Retest requested. The issue stays open until your retest passes.'

gh issue edit 4 --repo "$R" --add-label "confirmed,minor,retest-requested,fixed"
gh issue comment 4 --repo "$R" --body 'Fix commit <H> (branch fix/redteam-2). Cold calls fail fast with JSON-RPC -32004 when the controller reports two failed scale-ups in a row (/v1/state scale_failing), instead of blocking for cold_timeout_s. Test: tests/test_cold_fail_fast.py. Retest requested. The issue stays open until your retest passes.'

gh issue edit 5 --repo "$R" --add-label "confirmed,minor,retest-requested"
gh issue comment 5 --repo "$R" --body 'Fix commit <H> (branch fix/redteam-2). Confirmed. Related to #23 but not the same root cause. The direct unauthenticated POST /v1/warm route is closed (admin token required). NOT fixed: cost amplification through POST /mcp demand warms (stateless MCP gateway; bounded by max_total 20, 2 starts/node, TTL eviction). Workaround: opt-in NetworkPolicy. Details: docs/handoff/TRIAGE_TABLE.md. Retest requested. The issue stays open until your retest passes.'

gh issue edit 6 --repo "$R" --add-label "confirmed,minor,retest-requested,fixed"
gh issue comment 6 --repo "$R" --body 'Fix commit <H> (branch fix/redteam-2). CSV exports (/api/export.csv, /api/export_turns.csv) prefix text cells starting with = + - @ (tab, CR) with '\''. Test: tests/test_console_csv_injection.py. Retest requested. The issue stays open until your retest passes.'

gh issue edit 8 --repo "$R" --add-label "confirmed,minor,retest-requested,fixed"
gh issue comment 8 --repo "$R" --body 'Fix commit <H> (branch fix/redteam-2). skypiea status / policy get now report the predictor the gateway actually uses (gateway SKYPIEA_PREDICTOR_URL); policy set refuses with a clear message when the gateway does not use the stub (--force overrides). Test: tests/test_cli_effective_policy.py. Retest requested. The issue stays open until your retest passes.'

gh issue edit 9 --repo "$R" --add-label "confirmed,major,retest-requested,fixed"
gh issue comment 9 --repo "$R" --body 'Fix commit <H> (branch fix/redteam-2). A yes with any negation is no longer consent (the engine shows the quote and asks again); action keywords match whole words only. Test: tests/test_guard_negated_yes.py. Retest requested. The issue stays open until your retest passes.'

gh issue edit 10 --repo "$R" --add-label "confirmed,major,retest-requested,fixed"
gh issue comment 10 --repo "$R" --body 'Fix commit <H> (branch fix/redteam-2). A confirmed write must match the quoted target (booking code, flights, promo, bags, passengers, fare); a write that omits the target uses the quoted one. Test: tests/test_guard_quote_target.py. Retest requested. The issue stays open until your retest passes.'

gh issue edit 11 --repo "$R" --add-label "confirmed,major,retest-requested,fixed"
gh issue comment 11 --repo "$R" --body 'Fix commit <H> (branch fix/redteam-2). ec2:CreateTags is allowed only on create (ec2:CreateAction RunInstances / CreateSecurityGroup / AuthorizeSecurityGroupEgress) with aws:RequestTag/project=skypiea; RunInstances and CreateSecurityGroup must carry the tag. Repository change only: the live AWS account was not changed. Test: tests/test_aws_policy_tags.py. Retest requested. The issue stays open until your retest passes.'

gh issue edit 12 --repo "$R" --add-label "confirmed,minor,retest-requested,fixed"
gh issue comment 12 --repo "$R" --body 'Fix commit <H> (branch fix/redteam-2). The predictor contract now bounds start_in_ms (<= 600000), ttl_s (<= 86400) and decisions (<= 64); anything else is off-contract and ignored (FR-4). Test: tests/test_contract_bounds.py. Retest requested. The issue stays open until your retest passes.'

gh issue edit 13 --repo "$R" --add-label "confirmed,major,retest-requested,fixed"
gh issue comment 13 --repo "$R" --body 'Fix commit <H> (branch fix/redteam-2). iam:CreateRole / iam:PutRolePolicy on skypiea-node require the skypiea-node-boundary permissions boundary (infra/aws/node-boundary-policy.json); removing or editing the boundary and attaching managed policies are explicitly denied; main.tf sets the boundary. Repository change only: the live AWS account was not changed. Test: tests/test_aws_policy_boundary.py. Retest requested. The issue stays open until your retest passes.'

gh issue edit 14 --repo "$R" --add-label "confirmed,major,retest-requested,fixed"
gh issue comment 14 --repo "$R" --body 'Fix commit <H> (branch fix/redteam-2). Decision P3 implemented: the gateway keeps and sends the last 200 calls plus history_total, so prewarming continues past call 1,000. Test: tests/test_gateway_history_window.py. Retest requested. The issue stays open until your retest passes.'

gh issue edit 15 --repo "$R" --add-label "confirmed,major,retest-requested,fixed"
gh issue comment 15 --repo "$R" --body 'Fix commit <H> (branch fix/redteam-2). POST /mcp and /v1/hint refuse bodies over 1 MiB (configurable, platform.gateway.maxBodyBytes) with 413, from Content-Length before reading and while streaming. Test: tests/test_gateway_body_limit.py. Retest requested. The issue stays open until your retest passes.'

gh issue edit 16 --repo "$R" --add-label "confirmed,major,retest-requested,fixed"
gh issue comment 16 --repo "$R" --body 'Fix commit <H> (branch fix/redteam-2). The gateway never re-sends a tools/call whose connection dropped after it was sent; the client gets JSON-RPC -32003 (the tool may have run; not retried). Only connect errors are retried. Test: tests/test_gateway_no_replay.py. Retest requested. The issue stays open until your retest passes.'

gh issue edit 17 --repo "$R" --add-label "confirmed,minor,retest-requested,fixed"
gh issue comment 17 --repo "$R" --body 'Fix commit <H> (branch fix/redteam-2). book_flight refuses n passengers when n > seats_left and decrements seats_left on a confirmed booking; cancel returns the seats, change_flight moves them; each conversation has its own copy. Test: tests/test_skyair_issue17.py. Retest requested. The issue stays open until your retest passes.'

gh issue edit 18 --repo "$R" --add-label "confirmed,major,retest-requested,fixed"
gh issue comment 18 --repo "$R" --body 'Fix commit <H> (branch fix/redteam-2). Tool calls, the predictor and the controller now use separate httpx pools (200 / 50 / 20). The load test from the issue was not re-run by us. Test: tests/test_gateway_pools.py. Retest requested. The issue stays open until your retest passes.'

gh issue edit 19 --repo "$R" --add-label "confirmed,major,retest-requested,fixed"
gh issue comment 19 --repo "$R" --body 'Fix commit <H> (branch fix/redteam-2). Write quotes expire at the next user turn unless it is a plain yes (declined, new request or stale); recorded as a quote_expired guard. Test: tests/test_guard_quote_expiry.py. Retest requested. The issue stays open until your retest passes.'

gh issue edit 20 --repo "$R" --add-label "confirmed,major,retest-requested,fixed"
gh issue comment 20 --repo "$R" --body 'Fix commit <H> (branch fix/redteam-2). Timeouts on every Kubernetes call, the reconcile loop survives a hung patch, /healthz answers 503 when no tick finished for 60 s, and the controller has a livenessProbe. Test: tests/test_controller_timeouts.py. Retest requested. The issue stays open until your retest passes.'

gh issue edit 21 --repo "$R" --add-label "confirmed,minor,retest-requested,fixed"
gh issue comment 21 --repo "$R" --body 'Fix commit <H> (branch fix/redteam-2). change_flight to the flight the booking is already on answers error same_flight; nothing is charged. Test: tests/test_skyair_issue21.py. Retest requested. The issue stays open until your retest passes.'

gh issue edit 22 --repo "$R" --add-label "confirmed,minor,retest-requested,fixed"
gh issue comment 22 --repo "$R" --body 'Fix commit <H> (branch fix/redteam-2). apply_promo computes a percentage of the fare only (flights at the booking'\''s fare x passengers), matching the book-time path. Test: tests/test_skyair_issue22.py. Retest requested. The issue stays open until your retest passes.'

gh issue edit 23 --repo "$R" --add-label "confirmed,major,retest-requested,fixed"
gh issue comment 23 --repo "$R" --body 'Fix commit <H> (branch fix/redteam-2). POST /v1/warm needs the admin token; every field is bounded (400, never 500); the metric label is a fixed set; the refused list is drained after counting. Test: tests/test_controller_warm_input.py. Retest requested. The issue stays open until your retest passes.'

gh issue edit 24 --repo "$R" --add-label "confirmed,minor,retest-requested,fixed"
gh issue comment 24 --repo "$R" --body 'Fix commit <H> (branch fix/redteam-2). A wrong --context or missing/bad config now gives one clean line and a non-zero exit (CLI 1, replayer 2), no traceback. Test: tests/test_clean_errors.py. Retest requested. The issue stays open until your retest passes.'

gh issue edit 25 --repo "$R" --add-label "confirmed,major,retest-requested,fixed"
gh issue comment 25 --repo "$R" --body 'Fix commit <H> (branch fix/redteam-2). Tool calls written as text are gated by the tools offered for that step, so on a flagged turn (tools=[]) they are hidden and never executed. Test: tests/test_guard_text_call_suppressed.py. Retest requested. The issue stays open until your retest passes.'

gh issue edit 26 --repo "$R" --add-label "confirmed,major,retest-requested,fixed"
gh issue comment 26 --repo "$R" --body 'Fix commit <H> (branch fix/redteam-2). New histogram skypiea_gateway_call_latency_seconds{tool,cold} and Grafana panel '\''Per-call latency, cold vs warm (FR-5)'\''. Test: tests/test_fr5_cold_warm_histogram.py. Retest requested. The issue stays open until your retest passes.'

gh issue edit 27 --repo "$R" --add-label "confirmed,major,retest-requested,fixed"
gh issue comment 27 --repo "$R" --body 'Fix commit <H> (branch fix/redteam-2). POST /admin/policy on the stub predictor needs the admin token (GET stays read-only); params validated at set time (400, never stored); /decide fails open to '\''nothing warmed'\''; per-session state LRU-capped. Test: tests/test_predictor_stub_admin.py. Retest requested. The issue stays open until your retest passes.'

