#!/usr/bin/env bash
# Morning driver (orders 2026-11-05, 05:30 UTC): same machinery as overnight.sh (short SSM
# calls, 10-min polls, max 2 retries per Job/policy with identical parameters, every attempt's
# log kept). Steps in priority order; no new step starts after FREEZE (07:30 UTC):
#   1. platform image 38c62ec (stub floors ranked p), gateway -> stub; A markov1 + keep_alive
#      T=10/60 s (one Job), then B markov1
#   2. B keep_alive T=10/60 s
#   3. 40-session check, real predictor (coding), session draw without non-finite calls
#   4. only if predictor timeouts recur: ONE higher predictor CPU request, rerun step 3
# Gateway predictor_errors by reason are snapshotted before/after every Job ($OUT/<step>.gw).
set -uo pipefail
cd "$(dirname "$0")/../../.."
source <(sed '/^say "overnight driver start"/,$d' infra/aws/scripts/overnight.sh)
PLAT_TAG=38c62ec
FREEZE=0730
CPU_UP=500m     # step 4: predictor CPU request 100m -> 500m (node: 2 vCPU)
frozen() { [ "$(date -u +%H%M)" -ge "$FREEZE" ]; }

gw_errors() {   # predictor_errors / prewarm lines of the gateway's /metrics (via the API proxy)
  # one direct short SSM call (not ssm(): an empty answer must not trigger its 10-min retry)
  timeout 60 "$NODE_RUN" "k3s kubectl get --raw /api/v1/namespaces/skypiea-tools/services/skypiea-gateway:80/proxy/metrics 2>&1 | grep -E '^skypiea_gateway_(predictor_errors|prewarm)'; echo '# end'" 2>&1 | grep -E '^skypiea_gateway_|^# end|rror' || true
}
# guarded scenario step: snapshot gateway counters around it; skip when frozen
mstep() {
  local step=$1; shift
  grep -qx "$step" "$OUT/done" && return 0
  frozen && { say "$step: after 07:30 UTC freeze, not started"; echo "$step" >> "$OUT/done"; return 0; }
  { echo "# before $(date -u +%H:%M:%S)"; gw_errors; } > "$OUT/$step.gw"
  scenario "$step" "$@"
  { echo "# after $(date -u +%H:%M:%S)"; gw_errors; } >> "$OUT/$step.gw"
}

say "morning driver start"
if ! grep -qx m_deploy "$OUT/done"; then
  helm_set --set platformImageTag=$PLAT_TAG --set platform.predictor.serve=stub || exit 3
  echo m_deploy >> "$OUT/done"; say "platform $PLAT_TAG, gateway -> stub"
fi
mstep A_m2 A markov1,keep_alive_10,keep_alive_60 "$REAL" real
mstep B_m1r B markov1 "$REAL" real
mstep B_ka B keep_alive_10,keep_alive_60 "$REAL" real

conc() {   # NAME: 40-session check with the real predictor, up to 3 attempts
  local name=$1 att st
  for att in 1 2 3; do
    frozen && { say "$name a$att: after freeze, not started"; return 0; }
    { echo "# before $(date -u +%H:%M:%S)"; gw_errors; } > "$OUT/${name}_a$att.gw"
    st=$(run_job "${name}_a$att" CHECK=concurrent FROM_TEST=B N=40 POL=predictive \
         RECORD=results/concurrent_sessions.json DEADLINE=2700 | tail -1)
    { echo "# after $(date -u +%H:%M:%S)"; gw_errors; } >> "$OUT/${name}_a$att.gw"
    say "$name a$att: job $st; $(grep -E '^FAILURES' "$OUT/${name}_a$att.log")"
    grep -q '^FAILURES: none' "$OUT/${name}_a$att.log" && return 0
  done
}
timeouts() {   # predictor timeouts during attempt file $1 (after - before)
  awk '/^# after/{a=1} /^skypiea_gateway_predictor_errors_total\{reason="timeout"\}/{v=$NF; if(a) t+=v; else t-=v} END{print t+0}' "$1"
}
if ! grep -qx m_model "$OUT/done"; then
  frozen && { say "conc40: after freeze, not started"; say "MORNING DONE"; exit 0; }
  helm_set --set platform.predictor.serve=model --set platform.predictor.profile=coding || exit 3
  echo m_model >> "$OUT/done"; say "gateway -> model (coding)"
fi
if ! grep -qx conc40m "$OUT/done"; then conc conc40m; echo conc40m >> "$OUT/done"; fi
last=$(ls "$OUT"/conc40m_a*.gw 2>/dev/null | sort | tail -1)
to=$([ -n "$last" ] && timeouts "$last" || echo 0)
say "conc40m: predictor timeouts in last attempt: $to"
if [ "$to" -gt 0 ] && ! grep -qx conc40cpu "$OUT/done"; then
  if frozen; then say "step 4: after freeze, not started"
  else
    helm_set --set platform.predictor.modelResources.requests.cpu=$CPU_UP || exit 3
    say "predictor cpu request -> $CPU_UP"; conc conc40cpu
  fi
  echo conc40cpu >> "$OUT/done"
fi
say "MORNING DONE"
