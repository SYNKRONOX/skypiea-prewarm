#!/usr/bin/env bash
# Phase B verification matrix (DEMO RUNS, NOT RESULTS): Skye autopilot scenarios with the
# hint off and on typing, plus one predictor-kill run. Needs the live console (make live).
# Output: .live/runs/phaseb_<scenario>_<hint>/ (git-ignored; contains conversation text).
set -uo pipefail
cd "$(dirname "$0")/.."
PY="${PY:-.venv/bin/python}"
for sc in ${SCENARIOS:-change cancel_refund failed_verification ambiguous_name unknown_tool}; do
  for h in ${HINTS:-off typing}; do
    echo "== $sc hint=$h $(date -u +%H:%M:%S)"
    "$PY" -m demo.console_run --demo skyair --scenario "$sc" --hint "$h" \
      --name "phaseb_${sc}_${h}" || echo "!! $sc $h failed"
  done
done
if [ -z "${NO_KILL:-}" ]; then
  echo "== change predictor-kill $(date -u +%H:%M:%S)"
  "$PY" -m demo.console_run --demo skyair --scenario change --hint typing --kill-predictor \
    --name phaseb_change_kill || echo "!! kill run failed"
fi
echo "== done $(date -u +%H:%M:%S)"
