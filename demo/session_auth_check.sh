#!/usr/bin/env bash
# Issue #7 on a real cluster: the gateway's session introspection needs the admin token.
#   1. GET /v1/sessions WITHOUT the token (API-server pod proxy, no X-Admin-Token) is refused
#      and returns no session id;
#   2. the CLI (token read from Secret skypiea-admin with the same kubeconfig) is accepted.
# Never prints the token.
set -euo pipefail
cd "$(dirname "$0")/.."
CTX="${KIND_CTX:-kind-${CLUSTER:-skypiea}}"
NS="${NS:-skypiea-tools}"
pod=$(kubectl --context "$CTX" -n "$NS" get pod -l skypiea.io/tool=skypiea-gateway \
      --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}')
[ -n "$pod" ] || { echo "session-auth: no running gateway pod in $NS" >&2; exit 1; }
kubectl --context "$CTX" -n "$NS" get secret skypiea-admin >/dev/null \
  || { echo "session-auth: Secret skypiea-admin missing in $NS" >&2; exit 1; }
if out=$(kubectl --context "$CTX" get --raw \
         "/api/v1/namespaces/$NS/pods/$pod:8080/proxy/v1/sessions" 2>&1); then
  echo "session-auth: FAIL, unauthenticated GET /v1/sessions answered: ${out:0:200}" >&2; exit 1
fi
if grep -q session_id <<<"$out"; then
  echo "session-auth: FAIL, the refusal contains session ids" >&2; exit 1
fi
echo "session-auth: unauthenticated GET /v1/sessions refused (${out:0:80})"
bin/skypiea --context "$CTX" -n "$NS" sessions >/dev/null
echo "session-auth: skypiea sessions with the Secret's token: accepted"
