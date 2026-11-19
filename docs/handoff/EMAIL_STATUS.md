# Status for the red teams (19 November 2026)

Fixes are on branch `fix/redteam-2` (not merged yet), plus `fix/redteam-1` (merged) for #7.
Every issue stays open until your retest passes. Full table: `docs/handoff/TRIAGE_TABLE.md`.

## Cloudhack (Red Team 2)

- #2 `make doctor` on a Tier 1 machine: fixed (`6899ecb`), retest requested.
- #3 base image digest: fixed (`3b6e2ac`; images not rebuilt or rescanned by us), retest requested.
- #4 120 s block when the controller cannot scale: fixed, fails fast with -32004 (`0699e5c`), retest requested.
- #5 warm-pool cost amplification: partly fixed (`20a94bf`: `/v1/warm` needs the admin token); amplification through `/mcp` demand warms remains, bounded by the caps; retest requested.
- #6 CSV formula injection: fixed (`a9a7de3`), retest requested.
- #7 session enumeration: fixed, retest passed (thank you).
- #21 same-flight change fee: fixed (`eb9da86`), retest requested.
- #22 promo over-discount: fixed (`4b32a0d`), retest requested.
- #24 tracebacks: fixed (`b5c4f0c`), retest requested.

## Altus

- #8 CLI policy status: fixed (`0589485`), retest requested.
- #9 negated yes: fixed (`45d7635`), retest requested.
- #10 quote target: fixed (`1affef7`), retest requested.
- #11 `ec2:CreateTags`: fixed in the repo (`f58b3db`; the live AWS account was not changed), retest requested.
- #12 `start_in_ms` bound: fixed (`e684663`), retest requested.
- #13 node-role escalation: fixed in the repo (`df8b6c0`; the live AWS account was not changed), retest requested.
- #14 prewarming after call 1,000: fixed (`c3f5c39`), retest requested.
- #15 body size: fixed (`fc3e36f`), retest requested.
- #16 duplicate `tools/call`: fixed (`220fa18`), retest requested.
- #17 seats_left: fixed (`bedd0a2`), retest requested.
- #18 shared httpx pool: fixed (`7733ebc`), retest requested.
- #19 stale quotes: fixed (`4a56dba`), retest requested.
- #20 hung Kubernetes patch: fixed (`db1ad7e`), retest requested.
- #23 `/v1/warm` validation: fixed (`20a94bf`), retest requested.
- #25 text-call injection bypass: fixed (`5582f9d`), retest requested.
- #26 FR-5 histogram: fixed (`9e9e510`), retest requested.
- #27 predictor admin endpoint: fixed (`cd8a4d9`), retest requested.

Hashes are from the fix branch and may change on merge; the issue comments will carry the
final ones.
