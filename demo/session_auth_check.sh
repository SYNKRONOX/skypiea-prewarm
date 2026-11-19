#!/usr/bin/env bash
# Issue #7 on a real cluster: the gateway's session introspection needs the admin token.
#   1. the gateway runs with ADMIN_TOKEN from Secret skypiea-admin, and no other workload in the
#      namespace references that Secret;
#   2. GET /v1/sessions and /v1/sessions/{sid} straight to the gateway pod (loopback
#      port-forward) WITHOUT a token and with a WRONG token answer the gateway's own 401 and
#      contain no session id;
#   3. the CLI (token read from the Secret with the same kubeconfig) is accepted.
# Never prints the token (the wrong one is random; the right one is only used by the CLI).
set -euo pipefail
cd "$(dirname "$0")/.."
CTX="${KIND_CTX:-kind-${CLUSTER:-skypiea}}"
SKYPIEA="${SKYPIEA_BIN:-bin/skypiea}"   # tests/test_session_auth_check.py swaps it
NS="${NS:-skypiea-tools}"
k() { kubectl --context "$CTX" -n "$NS" "$@"; }
fail() { echo "session-auth: FAIL, $*" >&2; exit 1; }

pod=$(k get pod -l skypiea.io/tool=skypiea-gateway --field-selector=status.phase=Running \
      -o jsonpath='{.items[0].metadata.name}')
[ -n "$pod" ] || fail "no running gateway pod in $NS"
k get secret skypiea-admin >/dev/null || fail "Secret skypiea-admin missing in $NS"
src=$(k get deploy skypiea-gateway \
      -o jsonpath='{.spec.template.spec.containers[0].env[?(@.name=="ADMIN_TOKEN")].valueFrom.secretKeyRef.name}')
[ "$src" = skypiea-admin ] || fail "gateway ADMIN_TOKEN does not come from Secret skypiea-admin"
# the token holders: the gateway (sessions, #7), the controller (/v1/warm, #23) and the stub
# predictor (/admin/policy, #27); never a tool pod or anything else
users=$(k get deploy,statefulset,daemonset,job,cronjob -o json | python3 -c '
import json, sys
print(" ".join(sorted(o["kind"] + "/" + o["metadata"]["name"]
               for o in json.load(sys.stdin)["items"] if "skypiea-admin" in json.dumps(o["spec"]))))')
allowed=" Deployment/skypiea-gateway Deployment/skypiea-controller Deployment/skypiea-predictor "
for u in $users; do
  case "$allowed" in *" $u "*) ;; *) fail "Secret skypiea-admin referenced by: $u (users: $users)" ;; esac
done
echo "session-auth: Secret skypiea-admin referenced only by: $users"

log=$(mktemp); out=$(mktemp)
k port-forward --address 127.0.0.1 "pod/$pod" :8080 >"$log" 2>&1 & pf=$!
trap 'kill $pf 2>/dev/null; rm -f "$log" "$out"' EXIT
port=""
for _ in $(seq 1 40); do
  port=$(sed -nE 's/^Forwarding from 127\.0\.0\.1:([0-9]+) .*/\1/p' "$log" | head -1)
  [ -n "$port" ] && break; sleep 0.25
done
[ -n "$port" ] || fail "port-forward to $pod did not start: $(head -c 200 "$log")"
for path in /v1/sessions /v1/sessions/no-such-session; do
  for hdr in "" "X-Admin-Token: wrong-$RANDOM$RANDOM$RANDOM"; do
    code=$(curl -s -m 5 -o "$out" -w '%{http_code}' ${hdr:+-H "$hdr"} "http://127.0.0.1:$port$path")
    what=$([ -n "$hdr" ] && echo "wrong token" || echo "no token")
    [ "$code" = 401 ] || fail "GET $path with $what answered $code: $(head -c 200 "$out")"
    grep -q 'admin token' "$out" || fail "GET $path with $what: not the gateway's refusal"
    if grep -q session_id "$out"; then fail "GET $path with $what: refusal contains session ids"; fi
    echo "session-auth: GET $path with $what -> 401 ($(head -c 60 "$out"))"
  done
done
"$SKYPIEA" --context "$CTX" -n "$NS" sessions >/dev/null
echo "session-auth: skypiea sessions with the Secret's token: accepted"
