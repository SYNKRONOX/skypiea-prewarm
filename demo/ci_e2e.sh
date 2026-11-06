#!/usr/bin/env bash
# End-to-end check used by CI (.github/workflows/ci.yml, job "e2e") and runnable locally:
#   1 make doctor (demo checks)         stop early with the fix if the machine is not ready
#   2 make demo                         kind + images + chart + PRD Scenario A replay under
#                                       scale_to_zero and predictive; fails on any failed call
#   3 make agent-demo LLM=scripted      the agent through the gateway, scripted LLM (no Ollama,
#                                       no GPU, no network); fails on any failed call
#   4 every tool Deployment back to 0   within E2E_ZERO_TIMEOUT seconds (default 300)
# On a failure it prints pods, events and the platform logs. It does NOT delete the cluster
# (CI runners are thrown away; locally run `make demo-clean` with the same CLUSTER=...).
# Use another cluster and ports so an existing `skypiea` cluster is not touched:
#   CLUSTER=skypiea-rt REG_PORT=5002 REG_NAME=kind-registry-rt PF_BASE=28080 GRAFANA_PORT=3300 \
#     demo/ci_e2e.sh
set -euo pipefail
cd "$(dirname "$0")/.."
export CLUSTER="${CLUSTER:-skypiea}"
export KIND_CTX="${KIND_CTX:-kind-$CLUSTER}"
NS="${NS:-skypiea-tools}"
ZERO_TIMEOUT="${E2E_ZERO_TIMEOUT:-300}"
T0=$(date +%s)
say() { echo; echo "=== e2e [$(( $(date +%s) - T0 ))s] $*"; }
dump() {
  echo "--- e2e FAILED: diagnostics ($KIND_CTX / $NS)"
  kubectl --context "$KIND_CTX" -n "$NS" get deploy,pods -o wide 2>&1 | head -60 || true
  kubectl --context "$KIND_CTX" -n "$NS" get events --sort-by=.lastTimestamp 2>&1 | tail -30 || true
  for d in skypiea-gateway skypiea-controller skypiea-model; do
    echo "--- logs $d"; kubectl --context "$KIND_CTX" -n "$NS" logs "deploy/$d" --tail=40 2>&1 || true
  done
  for f in .demo/run_*.log .demo/pf/*.log; do [ -f "$f" ] && { echo "--- $f"; tail -20 "$f"; }; done
  return 0
}
trap 'rc=$?; [ $rc = 0 ] || dump; exit $rc' EXIT

say "1/4 make doctor (demo)"
make --no-print-directory doctor DOCTOR_MODE=demo

say "2/4 make demo (cluster $CLUSTER)"
make --no-print-directory demo

say "3/4 make agent-demo LLM=scripted (1 session per scenario)"
make --no-print-directory agent-demo LLM=scripted AGENT_N="${E2E_AGENT_N:-1}"

say "4/4 every tool Deployment back to 0 replicas (timeout ${ZERO_TIMEOUT}s)"
end=$(( $(date +%s) + ZERO_TIMEOUT ))
while :; do
  up=$(kubectl --context "$KIND_CTX" -n "$NS" get deploy -l skypiea.io/tier \
        -o jsonpath='{range .items[?(@.spec.replicas>0)]}{.metadata.name}={.spec.replicas} {end}')
  n=$(kubectl --context "$KIND_CTX" -n "$NS" get deploy -l skypiea.io/tier -o name | wc -l)
  [ "$n" -gt 0 ] || { echo "no tool Deployments found in $NS" >&2; exit 1; }
  if [ -z "$up" ]; then echo "all $n tool Deployments at 0 replicas"; break; fi
  [ "$(date +%s)" -lt "$end" ] || { echo "still scaled up after ${ZERO_TIMEOUT}s: $up" >&2; exit 1; }
  sleep 5
done
say "PASS (demo replay, scripted agent, scale back to zero)"
