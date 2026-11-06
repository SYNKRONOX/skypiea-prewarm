#!/usr/bin/env bash
# The lead-time demo grid (docs/handoff/LEADTIME_REPORT.md): per demo, hint {off, enter,
# typing} x lookahead {1, 2} x 3 autopilot conversations (fixed customer script, typing model
# 200 chars/min in every cell). Hint order rotates per repetition (Latin square). DEMO RUNS.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PY:-.venv/bin/python}"
OUT="${OUT:-.live/runs/leadtime}"
DEMOS="${DEMOS:-airline codefix}"
mkdir -p "$OUT"
orders=("off enter typing" "enter typing off" "typing off enter")
first_la=1
for demo in $DEMOS; do
  [ "$demo" = codefix ] && las="2 1" || las="1 2"     # one helm switch fewer
  for la in $las; do
    for rep in 1 2 3; do
      for mode in ${orders[$((rep - 1))]}; do
        name="${demo}_la${la}_${mode}_r${rep}"
        [ -f "$OUT/$name/summary.json" ] && { echo "skip $name"; continue; }
        echo "== $(date -u +%T) $name"
        "$PY" -m demo.console_run --name "$name" --demo "$demo" --hint "$mode" \
          --lookahead "$la" --out "$OUT" | grep -E "^saved|^  t" || echo "!! $name failed"
      done
    done
  done
done
echo "== $(date -u +%T) grid done"
