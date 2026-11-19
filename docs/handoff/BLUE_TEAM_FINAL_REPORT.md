# Blue-team final report: Skypiea (predictive prewarming for MCP tool servers)

| | |
|---|---|
| Repository | `SYNKRONOX/skypiea-prewarm` |
| Red-team baseline | `rt-baseline` (`8f81eb7`) |
| Fix branches | `fix/redteam-1` (merged to `main` at `e29fb39`, issue #7); `fix/redteam-2` (from `e29fb39`, all other issues, **not merged**) |
| Red teams | Altus, Cloudhack (Red Team 2) |
| Date | 19 November 2026 |
| Source of truth | [`TRIAGE_TABLE.md`](TRIAGE_TABLE.md) (one row per issue, evidence and commit) |

## 1. Summary

| | Count | Issues |
|---|---:|---|
| Issues received | 26 | #2-#27 |
| By team | Altus 17, Cloudhack 9 | |
| By severity (from the issue body) | Major 15, Minor 11 | |
| Confirmed | 26 | none duplicate, invalid, works-as-designed or cannot-reproduce |
| Fixed | 25 fully, 1 partly | partly: #5 |
| Retest passed | 1 | #7 (Cloudhack, on `e29fb39`) |
| Retest requested (pending) | 25 | every other issue |
| Retest failed | 0 | |

Every fix on `fix/redteam-2` has a regression test that was written first and failed before
the fix (Tier 0), except #3 (image digest, checked by running the new base image) and the
static AWS policy reviews #11 / #13 (policy tests). No fix on `fix/redteam-2` was run on a
cluster yet (CI runs Tier 1), and nothing was applied to AWS.

## 2. Findings and responses

| # | Team | Sev. | Finding | Response | Commit | Retest |
|---|---|---|---|---|---|---|
| 2 | Cloudhack | Minor | `make doctor` fails on a correct Tier 1 machine | missing Tier 2 bundles WARN in mode all, FAIL only in mode live | `6899ecb` | retest-requested |
| 3 | Cloudhack | Minor | outdated base image digest (fixable High CVE in apt) | current digest pinned (apt 2.6.2); not rebuilt / rescanned here | `3b6e2ac` | retest-requested |
| 4 | Cloudhack | Minor | cold call blocks 120 s when the controller cannot scale | fail fast (-32004) after 2 failed scale-ups | `0699e5c` | retest-requested |
| 5 | Cloudhack | Minor | unauthenticated warm-pool cost amplification | direct `/v1/warm` needs the admin token; amplification via `/mcp` demand warms **not fixed** (bounded by caps) | `20a94bf` (partly) | retest-requested |
| 6 | Cloudhack | Minor | CSV formula injection in console exports | formula-leading cells prefixed with `'` | `a9a7de3` | retest-requested |
| 7 | Cloudhack | Major | unauthenticated session enumeration and cross-session access | admin token on session introspection, 256-bit ids, opt-in NetworkPolicy | `e5de779` (+ `5ef2734`, `bb56ec4`) | **retest-passed** |
| 8 | Altus | Minor | `skypiea status` reports the stub policy while the gateway uses the model | CLI reports the gateway's actual predictor; `policy set` refuses when it would have no effect | `0589485` | retest-requested |
| 9 | Altus | Major | "Yes, but do not cancel" treated as consent | any negation voids the yes; whole-word keywords | `45d7635` | retest-requested |
| 10 | Altus | Major | confirmation does not check the quoted booking | write must match the quoted target | `1affef7` | retest-requested |
| 11 | Altus | Major | unrestricted `ec2:CreateTags` bypasses tag scoping | tag only on create with `project=skypiea` (repo only, AWS not changed) | `f58b3db` | retest-requested |
| 12 | Altus | Minor | `start_in_ms` unbounded: long-sleeping tasks pile up | contract bounds `start_in_ms`, `ttl_s`, decisions | `e684663` | retest-requested |
| 13 | Altus | Major | operator can escalate through the node role | permissions boundary required and protected (repo only, AWS not changed) | `df8b6c0` | retest-requested |
| 14 | Altus | Major | prewarming stops after a session's 1,000th call | last 200 calls + `history_total` (decision P3) | `c3f5c39` | retest-requested |
| 15 | Altus | Major | no request-size limit on `POST /mcp` (OOM) | 1 MiB limit, 413 before buffering | `fc3e36f` | retest-requested |
| 16 | Altus | Major | gateway re-sends a `tools/call` after a mid-call drop (duplicate refund) | never re-sent once sent; clear -32003 error | `220fa18` | retest-requested |
| 17 | Altus | Minor | `book_flight` never reduces `seats_left` | seat inventory checked and updated (book, cancel, change) | `bedd0a2` | retest-requested |
| 18 | Altus | Major | one shared httpx pool starves predictor/controller | separate pools per plane | `7733ebc` | retest-requested |
| 19 | Altus | Major | declined/stale quote never expires | quotes expire on decline, new request or next turn | `4a56dba` | retest-requested |
| 20 | Altus | Major | one hung Kubernetes patch freezes scaling, `/healthz` stays 200 | timeouts, loop survives, stuck `/healthz`, livenessProbe | `db1ad7e` | retest-requested |
| 21 | Cloudhack | Minor | change fee for a same-flight change | refused, no fee | `eb9da86` | retest-requested |
| 22 | Cloudhack | Minor | "% off your fare" discounts baggage too | fare only | `4b32a0d` | retest-requested |
| 23 | Altus | Major | `/v1/warm` input exhausts memory / returns 500 | admin token, bounded fields, fixed labels, drained buffer | `20a94bf` | retest-requested |
| 24 | Cloudhack | Minor | tracebacks on a wrong `--context` / missing config | one clean line, non-zero exit | `b5c4f0c` | retest-requested |
| 25 | Altus | Major | text-written tool call bypasses the injection guard | text calls gated by the tools offered this step | `5582f9d` | retest-requested |
| 26 | Altus | Major | FR-5: no cold/warm-labelled histogram | `skypiea_gateway_call_latency_seconds{tool,cold}` + Grafana panel | `9e9e510` | retest-requested |
| 27 | Altus | Major | unauthenticated stub admin endpoint, persistent 500s, unbounded state | admin token, validation, fail-open `/decide`, LRU cap | `cd8a4d9` | retest-requested |

Commit hashes are those on `fix/redteam-2` before merge; the issue comments carry the final
hash (`<H>` in `label_script.sh`).

## 3. Not fixed

- **#5 (partly).** Cause: the stateless MCP gateway accepts `POST /mcp` from any in-cluster
  client, and each cold call asks for a demand warm. Impact: bounded extra idle cost
  (`max_total` 20, 2 starts per node, TTL eviction). Workaround: enable the opt-in
  NetworkPolicy and keep the gateway ClusterIP.

## 4. Verification and limits

- Tier 0 (`make test`, `ruff`, `make check-sizes`) after every commit on `fix/redteam-2`.
- Not done on this branch: a cluster run (`make demo`, `make live`, `make review` Tier 1),
  the red team's own load reproductions (#15, #18), an image rebuild and trivy rescan (#3),
  and any AWS change (#11, #13 are repository changes; an admin must create
  `skypiea-node-boundary` and re-attach the operator policy).
- Behaviour changes a user can see are listed in `docs/KNOWN_ISSUES.md` (admin token for
  scripts, -32003 / -32004 errors, `policy set` refusal).

## 5. Use of LLM assistance

Claude (Anthropic) in Claude Code read each issue, wrote the failing regression tests and the
fixes, ran the Tier 0 checks and drafted these documents. The blue team reviews the diffs and
runs Tier 1 / the retests; nothing here reports a retest result the red teams have not posted.
