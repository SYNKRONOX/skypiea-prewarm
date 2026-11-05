#!/usr/bin/env bash
# Follow-up to overnight.sh (orders 2026-11-04): after the planned steps, rerun B markov1 once
# (up to 2 retries) on the real test sessions with the warm-up fix (non-finite train timings
# dropped). Only allowed before 02:30 UTC. Gateway back to the stub (markov1 is a stub policy);
# every other parameter as in the original B baseline run. Reuses overnight.sh's functions.
set -uo pipefail
cd "$(dirname "$0")/../../.."
source <(sed '/^say "overnight driver start"/,$d' infra/aws/scripts/overnight.sh)
if [ "$(date -u +%H%M)" -ge 0230 ] && [ "$(date -u +%H%M)" -lt 1200 ]; then
  say "m1fix: after 02:30 UTC, skipped"; exit 0
fi
if ! grep -qx m1fix_stub "$OUT/done"; then
  helm_set --set platform.predictor.serve=stub || exit 3
  echo m1fix_stub >> "$OUT/done"; say "gateway -> stub for the B markov1 rerun"
fi
scenario B_m1fix B markov1 "$REAL" real
say "M1FIX DONE"
