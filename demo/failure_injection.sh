#!/usr/bin/env bash
# Failure injection on the demo stack (docs/DEMO.md): tool calls must still succeed.
#   1. predictor stopped (model and stub scaled to 0): the gateway falls back to the cold path
#   2. controller killed (pod deleted; the Deployment starts a new one)
# Leaves the stack as it found it (replicas restored).
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PY:-.venv/bin/python}"
CTX="${KIND_CTX:-kind-${CLUSTER:-skypiea}}"
NS="${NS:-skypiea-tools}"
K="kubectl --context $CTX -n $NS"
GW="http://127.0.0.1:${PF_BASE:-18080}/mcp"
demo/portforward.sh check || demo/portforward.sh start
rc=0

echo "== 1. predictor stopped (skypiea-model and skypiea-predictor scaled to 0)"
$K scale deploy/skypiea-model deploy/skypiea-predictor --replicas=0 >/dev/null
$K wait --for=delete pod -l skypiea.io/tool=skypiea-model --timeout=120s >/dev/null 2>&1 || true
"$PY" -m agent.probe --gateway "$GW" --tools order_lookup,policy_check,refund_issue || rc=1
$K scale deploy/skypiea-model deploy/skypiea-predictor --replicas=1 >/dev/null
$K rollout status deploy/skypiea-model --timeout=180s >/dev/null
$K rollout status deploy/skypiea-predictor --timeout=180s >/dev/null
echo "   predictor restored"

echo "== 2. controller killed (pod deleted)"
$K delete pod -l skypiea.io/tool=skypiea-controller --wait=false >/dev/null
"$PY" -m agent.probe --gateway "$GW" --tools read_file,run_tests,format_diff,post_comment || rc=1
$K rollout status deploy/skypiea-controller --timeout=180s >/dev/null
demo/portforward.sh start >/dev/null   # the controller port-forward died with its pod
echo "   controller back: $($K get deploy skypiea-controller -o jsonpath='{.status.readyReplicas}')/1 ready"
[ $rc = 0 ] && echo "PASS: every call succeeded" || echo "FAIL: some calls failed (see above)"
exit $rc
