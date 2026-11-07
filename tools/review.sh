#!/usr/bin/env bash
# `make review`: run Tier 0 (tests) and Tier 1 (local kind demo) in order and print a
# PASS/FAIL table with timings. `make review MODE=test` runs Tier 0 only.
#
# Tier 1 runs on its OWN kind cluster and registry, never on your `skypiea` cluster:
#   CLUSTER=skypiea-review REG_PORT=5002 REG_NAME=kind-registry-review PF_BASE=28080 GRAFANA_PORT=3300
# (override any of them in the environment). If that cluster or registry already exists, Tier 1
# refuses: it was not started by this run. At the end it deletes only the cluster and registry
# it created (KEEP=1 keeps them). It stops only the port-forwards this checkout started
# (pid files in .demo/). Docker images are kept (`make clean-images` removes them).
# Exit 0 only if every step passed. Log of every step: .review/<step>.log
set -uo pipefail
cd "$(dirname "$0")/.."
MODE="${MODE:-all}"
case "$MODE" in test|all) ;; *) echo "usage: make review [MODE=test|all]" >&2; exit 2 ;; esac
export CLUSTER="${REVIEW_CLUSTER:-skypiea-review}"
export REG_PORT="${REVIEW_REG_PORT:-5002}"
export REG_NAME="${REVIEW_REG_NAME:-kind-registry-review}"
export PF_BASE="${REVIEW_PF_BASE:-28080}"
export GRAFANA_PORT="${REVIEW_GRAFANA_PORT:-3300}"
export KIND_CTX="kind-$CLUSTER" KIND_REG="localhost:$REG_PORT"
LOG=.review
rm -rf "$LOG"; mkdir -p "$LOG"
rows=(); failed=0; created=0; t_all=$(date +%s)

step() {   # step <tier> <name> <command...>
  local tier=$1 name=$2; shift 2
  local s; s=$(date +%s)
  printf '=== [tier %s] %s ... ' "$tier" "$name"
  if "$@" >"$LOG/$name.log" 2>&1; then r=PASS; else r=FAIL; failed=1; fi
  local d=$(( $(date +%s) - s ))
  echo "$r (${d}s)"
  [ "$r" = PASS ] || { echo "    last lines of $LOG/$name.log:"; tail -5 "$LOG/$name.log" | sed 's/^/    | /'; }
  rows+=("| $tier | $name | $r | ${d} s |")
  [ "$r" = PASS ]
}

skip() { rows+=("| $1 | $2 | SKIP | $3 |"); }

# ---- Tier 0
step 0 doctor-test make --no-print-directory doctor DOCTOR_MODE=test &&
step 0 setup       make --no-print-directory setup &&
{ step 0 test      make --no-print-directory test
  step 0 lint      make --no-print-directory lint
  step 0 check-sizes make --no-print-directory check-sizes; }

# ---- Tier 1
if [ "$MODE" = all ]; then
  if [ "$failed" = 1 ]; then
    skip 1 demo "Tier 0 failed"
  elif kind get clusters 2>/dev/null | grep -qx "$CLUSTER" \
       || docker inspect "$REG_NAME" >/dev/null 2>&1; then
    echo "review: kind cluster '$CLUSTER' or registry '$REG_NAME' already exists; not started by" \
         "this run, so it is left alone. Remove it yourself, or pick another: REVIEW_CLUSTER=<name>" \
         "REVIEW_REG_NAME=<name> REVIEW_REG_PORT=<port> make review" >&2
    rows+=("| 1 | demo | FAIL | cluster $CLUSTER exists already (left alone) |"); failed=1
  else
    if step 1 doctor-demo make --no-print-directory doctor DOCTOR_MODE=demo; then
      created=1   # from here on, the cluster and registry (if any) are this run's
      step 1 demo make --no-print-directory demo &&
      { step 1 agent-demo    make --no-print-directory agent-demo
        step 1 demo-failures make --no-print-directory demo-failures
        step 1 cli-status    bin/skypiea --context "$KIND_CTX" status; }
    fi
    if [ "$created" = 1 ]; then
      if [ "${KEEP:-0}" = 1 ]; then
        echo "review: KEEP=1, cluster '$CLUSTER' left running. Remove: CLUSTER=$CLUSTER REG_NAME=$REG_NAME make demo-clean"
        skip 1 clean-up "KEEP=1"
      else
        step 1 clean-up make --no-print-directory demo-clean || true
      fi
    fi
  fi
else
  skip 1 demo "MODE=test"
fi

echo
echo "| Tier | Step | Result | Time |"
echo "|---|---|---|---|"
printf '%s\n' "${rows[@]}"
echo "Total $(( $(date +%s) - t_all )) s. Logs: $LOG/"
if [ "$failed" = 0 ]; then echo "review: PASS"; else echo "review: FAIL"; fi
exit "$failed"
