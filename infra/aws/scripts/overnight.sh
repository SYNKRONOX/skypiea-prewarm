#!/usr/bin/env bash
# Overnight driver (2026-11-04/05): run the remaining real-timing Jobs in order, with short SSM
# calls only (one status poll every POLL_S = 600 s), retries (max 2 per Job/policy, identical
# parameters, every attempt's log kept), and an SSM-unreachable cut-off (60 min). It never
# changes the platform image, gateway settings, policies or any parameter; the only helm
# changes are the planned ones: predictor image/profile and serve (stub|model).
#   overnight.sh            run from the first step not yet done (state in $OUT/done)
# Milestones go to stdout (one line each); full detail to $OUT/driver.log.
set -uo pipefail
cd "$(dirname "$0")/../../.."
OUT="${OUT:-$HOME/.cache/skypiea/scenario/logs}"
POLL_S="${POLL_S:-600}"
PRED_TAG=af000231bc7b
mkdir -p "$OUT"; touch "$OUT/done"
export SKYPIEA_INSTANCE_ID=$(terraform -chdir=infra/aws output -raw instance_id)
NODE_RUN=infra/aws/scripts/node-run.sh
JOB=infra/aws/scripts/replay-job.sh
REAL=$HOME/.cache/skypiea/scenario/sessions.json
PRD=$HOME/.cache/skypiea/scenario/prd_sessions.json
log() { echo "$(date -u +%H:%M:%S) $*" >> "$OUT/driver.log"; }
say() { log "$*"; echo "$(date -u +%H:%M) $*"; }

# one short SSM call, retried every 10 min; after 60 min unreachable: give up (exit 9)
ssm() {
  local t0=$(date +%s) o
  while true; do
    if o=$(timeout 60 "$NODE_RUN" "$1" 2>&1); then echo "$o"; return 0; fi
    log "ssm failed: $(echo "$o" | tail -1)"
    if echo "$o" | grep -q "TargetNotConnected\|timed out\|Could not connect\|ExpiredToken\|EndpointConnectionError" \
       || [ -z "$o" ]; then
      [ $(( $(date +%s) - t0 )) -ge 3600 ] && { say "SSM unreachable for 60 min: giving up"; exit 9; }
      sleep 600; continue
    fi
    echo "$o"; return 1     # the command itself failed (reachable): caller decides
  done
}

helm_set() {   # planned value changes only, via a bounded tunnel; then wait for the rollouts
  local i
  for i in 1 2 3; do
    if TUNNEL_MAX_S=150 infra/aws/scripts/with-tunnel.sh helm upgrade skypiea charts/skypiea \
        --kubeconfig "$HOME/.kube/skypiea-aws" -n skypiea-tools --reuse-values "$@" \
        >> "$OUT/driver.log" 2>&1; then break; fi
    log "helm attempt $i failed"; [ $i = 3 ] && { say "helm failed 3x: $*"; return 1; }
    sleep 60
  done
  for i in $(seq 1 10); do
    o=$(ssm "k3s kubectl -n skypiea-tools rollout status deploy/skypiea-model --timeout=20s >/dev/null 2>&1 && k3s kubectl -n skypiea-tools rollout status deploy/skypiea-gateway --timeout=20s >/dev/null 2>&1 && echo READY; k3s kubectl -n skypiea-tools get deploy skypiea-model -o jsonpath='{.spec.template.spec.containers[0].image}{\" \"}{.spec.template.spec.containers[0].env[0].value}'; echo; k3s kubectl -n skypiea-tools get deploy skypiea-gateway -o jsonpath='{.spec.template.spec.containers[0].env[?(@.name==\"SKYPIEA_PREDICTOR_URL\")].value}'") || true
    if echo "$o" | grep -q READY; then
      log "rollout: $(echo "$o" | sed -E 's/[0-9]{12}/<acct>/g' | tr '\n' ' ')"; return 0
    fi
    sleep 30
  done
  say "rollout not ready after helm $*"; return 1
}

job_state() { ssm "k3s kubectl -n skypiea-tools get job skypiea-replay --no-headers 2>/dev/null | awk '{print \$2}'" | tail -1; }

wait_job() {   # poll every POLL_S until Complete/Failed
  local st
  while true; do
    st=$(job_state)
    log "job state: $st"
    case "$st" in Complete|Failed) echo "$st"; return 0 ;; esac
    sleep "$POLL_S"
  done
}

# run_job NAME VARS... : start the Job (VARS as env), wait, fetch NAME.log
run_job() {
  local name=$1; shift
  env "$@" "$JOB" start >> "$OUT/driver.log" 2>&1 || { log "start failed: $name"; return 1; }
  sleep 20
  local st; st=$(wait_job)
  "$JOB" fetch "$OUT/$name.log" >> "$OUT/driver.log" 2>&1
  log "$name: job $st, $(grep -c '^ROW ' "$OUT/$name.log" 2>/dev/null) rows"
  echo "$st"
}

# policies of POL that did not finish cleanly in LOG (no POLICY line with ok == calls)
failed_policies() {
  .venv/bin/python - "$1" "$2" <<'PY'
import json, sys
log, pols = sys.argv[1], sys.argv[2].split(",")
good = set()
try:
    for ln in open(log):
        if ln.startswith("POLICY "):
            d = json.loads(ln[7:])
            if d["ok"] == d["calls"] and d.get("model_decide_requests", 1) != 0:
                good.add(d["policy"])
except FileNotFoundError:
    pass
print(",".join(p for p in pols if p not in good))
PY
}

# scenario step with retries: STEP SCEN POL SESSIONS DATASET [PROFILE]
scenario() {
  local step=$1 scen=$2 pol=$3 sess=$4 ds=$5 prof=${6:-} todo att st
  grep -qx "$step" "$OUT/done" && { log "skip $step (done)"; return 0; }
  todo=$pol
  for att in 1 2 3; do
    say "$step: attempt $att, policies $todo"
    st=$(run_job "${step}_a$att" CHECK=scenario SCEN="$scen" POL="$todo" SESSIONS="$sess" \
         DATASET="$ds" PROFILE="$prof" DEADLINE=7200 | tail -1)
    todo=$(failed_policies "$OUT/${step}_a$att.log" "$todo")
    say "$step: attempt $att job $st; still failing: ${todo:-none}"
    [ -z "$todo" ] && break
  done
  [ -n "$todo" ] && say "$step: POLICY_ERROR after 3 attempts: $todo (continuing)"
  echo "$step" >> "$OUT/done"
}

decide() {   # STEP SCEN PROFILE SESSIONS (functional contract check, short)
  grep -qx "$1" "$OUT/done" && return 0
  run_job "$1" CHECK=decide SCEN="$2" PROFILE="$3" SESSIONS="$4" DEADLINE=300 > /dev/null
  say "$1: $(grep -c 'PASS' "$OUT/$1.log") PASS, $(grep -c 'FAIL' "$OUT/$1.log") FAIL"
  echo "$1" >> "$OUT/done"
}

say "overnight driver start"
# 1. the B baselines Job already running (started 21:56 by hand)
if ! grep -qx B_stub "$OUT/done"; then
  st=$(wait_job); "$JOB" fetch "$OUT/B_stub_a1.log" >> "$OUT/driver.log" 2>&1
  todo=$(failed_policies "$OUT/B_stub_a1.log" scale_to_zero,always_warm,keep_alive,markov1)
  say "B_stub: job $st; failing: ${todo:-none}"
  for att in 2 3; do
    [ -z "$todo" ] && break
    st=$(run_job "B_stub_a$att" CHECK=scenario SCEN=B POL="$todo" SESSIONS="$REAL" DATASET=real \
         DEADLINE=7200 | tail -1)
    todo=$(failed_policies "$OUT/B_stub_a$att.log" "$todo"); say "B_stub a$att: $st; failing: ${todo:-none}"
  done
  echo B_stub >> "$OUT/done"
fi
# 2. PRD (SYNTHETIC): image af000231bc7b, profile prd; baselines (stub) then predictive (model)
if ! grep -qx prd_deploy "$OUT/done"; then
  helm_set --set platform.predictor.image.tag=$PRED_TAG --set platform.predictor.profile=prd \
    --set platform.predictor.serve=stub || exit 3
  echo prd_deploy >> "$OUT/done"; say "deployed predictor $PRED_TAG profile=prd serve=stub"
fi
decide PRD_decide_A A prd "$PRD"
decide PRD_decide_B B prd "$PRD"
scenario PRDA_stub A scale_to_zero,always_warm,keep_alive,markov1 "$PRD" SYNTHETIC
scenario PRDB_stub B scale_to_zero,always_warm,keep_alive,markov1 "$PRD" SYNTHETIC
if ! grep -qx prd_model "$OUT/done"; then
  helm_set --set platform.predictor.serve=model || exit 3
  echo prd_model >> "$OUT/done"; say "gateway -> model (prd)"
fi
scenario PRDA_pred A predictive "$PRD" SYNTHETIC prd
scenario PRDB_pred B predictive "$PRD" SYNTHETIC prd
# 3. coding (retuned, same image): B predictive, real traces
if ! grep -qx coding_deploy "$OUT/done"; then
  helm_set --set platform.predictor.profile=coding || exit 3
  echo coding_deploy >> "$OUT/done"; say "profile=coding (serve=model)"
fi
decide B_decide_coding B coding "$REAL"
scenario B_pred B predictive "$REAL" real coding
# 4. 40-session check with the real predictor (TraceLab test sessions, gaps x0.2: functional)
if ! grep -qx conc40 "$OUT/done"; then
  for att in 1 2 3; do
    st=$(run_job "conc40_a$att" CHECK=concurrent FROM_TEST=B N=40 POL=predictive \
         RECORD=results/concurrent_sessions.json DEADLINE=2700 | tail -1)
    say "conc40 a$att: job $st; $(grep -E '^FAILURES' "$OUT/conc40_a$att.log")"
    grep -q '^FAILURES: none' "$OUT/conc40_a$att.log" && break
  done
  echo conc40 >> "$OUT/done"
fi
say "ALL RUNS DONE"
