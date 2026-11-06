#!/usr/bin/env bash
# Two Skypiea stacks in one kind cluster for the Live Console (docs/LIVE_CONSOLE.md):
#   skypiea-base  "Normal (baseline)"   : 16 tools at 0, gateway, controller, stub predictor
#                                         (policy scale_to_zero by default; keep_alive option)
#   skypiea-pred  "Skypiea (predictive)": 16 tools at 0, gateway (turn hint enabled),
#                                         controller, real predictor (profile support|coding),
#                                         Prometheus + Grafana watching BOTH namespaces
# Same images for both; each controller allows 2 concurrent starts on the one node, so up to
# 4 starts can compete (the console records it per call).
#
#   demo/live_stacks.sh up [support|coding]    build/push missing images, deploy both
#   demo/live_stacks.sh profile support|coding switch the predictive stack's profile
#   demo/live_stacks.sh lookahead 1|2          switch the predictive stack's lookahead
#   demo/live_stacks.sh pf                     (re)start loopback port-forwards
#   demo/live_stacks.sh pf-stop
#   demo/live_stacks.sh down                   uninstall both releases + namespaces
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PY:-.venv/bin/python}"
CTX="${KIND_CTX:-kind-skypiea}"
REG="${KIND_REG:-localhost:5001}"
# Real predictor bundles (support lgbm-support-aabdfd2, coding lgbm-coding-af00023, prd
# lgbm-prd-a7642d0). The console image is built from THIS checkout's service code (lookahead
# override) with those bundles, from $BUNDLES_DIR (default models/); without them the shipped
# image af000231bc7b is used (lookahead toggle unavailable).
BUNDLES_DIR="${BUNDLES_DIR:-models}"
BUNDLES="lgbm-coding-af00023 lgbm-support-aabdfd2 lgbm-prd-a7642d0"
SVC_SHA=$(git log -1 --format=%h -- services/predictor predictors configs/model)
PRED_IMAGE_TAG="${PRED_IMAGE_TAG:-}"
LIVE_CONSOLE_MAP="${LIVE_CONSOLE_MAP:-1}"   # console-only tool mapping (configs/live/console_tool_map.yaml)
STATE=.live/stack.env                     # profile and lookahead of the predictive stack
BASE=skypiea-base
PRED=skypiea-pred
PFDIR=.live/pf
mkdir -p "$PFDIR"

helm_q() {   # helm without the client-side throttling noise; fail on a real error
  local out rc
  out=$(helm "$@" 2>&1) && rc=0 || rc=$?
  echo "$out" | grep -v 'client-side throttling' | grep -v '^$' | tail -3 || true
  return $rc
}

images() {
  TAG=$(tools/image_tag.sh)
  TOOLS=$("$PY" -c 'import yaml;print(" ".join(yaml.safe_load(open("tools/tool_map.yaml"))["servers"]))')
  missing=""
  for t in $TOOLS; do docker image inspect "skypiea/$t:$TAG" >/dev/null 2>&1 || missing="$missing $t"; done
  [ -z "$missing" ] || TOOLS="$missing" WITH_PROBE=0 WITH_PLATFORM=0 TAG="$TAG" tools/build_images.sh
  docker image inspect "skypiea/platform:$TAG" >/dev/null 2>&1 || {
    echo "==> building skypiea/platform:$TAG"
    DOCKER_BUILDKIT=1 docker build --quiet -f deploy/platform/Dockerfile -t "skypiea/platform:$TAG" . >/dev/null; }
  for i in $TOOLS platform; do
    docker tag "skypiea/$i:$TAG" "$REG/skypiea/$i:$TAG"
    docker push -q "$REG/skypiea/$i:$TAG" >/dev/null
  done
  predictor_image
  if ! curl -sf "http://$REG/v2/skypiea/predictor/tags/list" | grep -q "\"$PRED_IMAGE_TAG\""; then
    if docker image inspect "skypiea/predictor:$PRED_IMAGE_TAG" >/dev/null 2>&1; then
      docker tag "skypiea/predictor:$PRED_IMAGE_TAG" "$REG/skypiea/predictor:$PRED_IMAGE_TAG"
      docker push -q "$REG/skypiea/predictor:$PRED_IMAGE_TAG" >/dev/null
    else
      echo "predictor image skypiea/predictor:$PRED_IMAGE_TAG (real support + coding bundles) is" \
           "not in $REG nor in docker. It is built from the datasets: see README 'Quickstart'" \
           "(make data-all, make model-all, make predictor-image), then PRED_IMAGE_TAG=<tag>." >&2
      exit 1
    fi
  fi
  echo "images: tools/platform $TAG, predictor $PRED_IMAGE_TAG"
}

predictor_image() {   # sets PRED_IMAGE_TAG; builds the console image if the bundles are here
  [ -n "$PRED_IMAGE_TAG" ] && return 0
  local ok=1 b
  for b in $BUNDLES; do [ -f "$BUNDLES_DIR/$b/MANIFEST.json" ] || ok=0; done
  if [ $ok = 0 ]; then
    PRED_IMAGE_TAG=af000231bc7b
    echo "bundles not in $BUNDLES_DIR: using the shipped predictor image $PRED_IMAGE_TAG (no lookahead toggle)"
    return 0
  fi
  PRED_IMAGE_TAG="console-$SVC_SHA"
  docker image inspect "skypiea/predictor:$PRED_IMAGE_TAG" >/dev/null 2>&1 && return 0
  if [ "$BUNDLES_DIR" != models ]; then
    mkdir -p models
    for b in $BUNDLES; do rm -rf "models/$b"; cp -r "$BUNDLES_DIR/$b" "models/$b"; done
  fi
  echo "==> building skypiea/predictor:$PRED_IMAGE_TAG (service $SVC_SHA + the three real bundles)"
  DOCKER_BUILDKIT=1 docker build --quiet -f services/predictor/Dockerfile \
    --build-arg CODING_DIR=models/lgbm-coding-af00023 \
    --build-arg SUPPORT_DIR=models/lgbm-support-aabdfd2 \
    --build-arg PRD_DIR=models/lgbm-prd-a7642d0 -t "skypiea/predictor:$PRED_IMAGE_TAG" . >/dev/null
}

map_args() {   # console mapping on BOTH stacks (same routing for baseline and Skypiea)
  if [ "$LIVE_CONSOLE_MAP" = 1 ]; then
    echo "--set-json=platform.classOverrides=$("$PY" -c 'import json,yaml;print(json.dumps(yaml.safe_load(open("configs/live/console_tool_map.yaml"))["overrides"],separators=(",",":")))')"
  fi
}

prepull() {   # every tool start is then a CACHED start (E0 "cached"), on both stacks alike
  local node n=0
  for node in $(kind get nodes --name "${CLUSTER:-skypiea}" | grep -v control-plane); do
    for t in $TOOLS; do
      docker exec "$node" crictl pull "$REG/skypiea/$t:$TAG" >/dev/null && n=$((n + 1))
    done
  done
  echo "pre-pulled $n tool images onto the worker node (every start is a cached start)"
}

deploy_base() {
  helm_q upgrade --install skypiea-base charts/skypiea --kube-context "$CTX" -n "$BASE" \
    --create-namespace -f charts/skypiea/values-kind.yaml --set imageTag="$TAG" \
    --set platform.enabled=true --set e0Probe.enabled=false --set monitoring.enabled=false \
    --set platform.predictor.policy=scale_to_zero $(map_args) --wait --timeout 10m
  echo "deployed $BASE (baseline: stub predictor, scale_to_zero)"
}

deploy_pred() {   # profile [lookahead]
  local la="${2:-1}" la_args=""
  [ "$la" = 2 ] && la_args='--set-json=platform.predictor.decideOverrides={"lookahead":2}'
  printf 'PROFILE=%s\nLOOKAHEAD=%s\nPRED_IMAGE_TAG=%s\nTAG=%s\n' "$1" "$la" "$PRED_IMAGE_TAG" \
    "$TAG" > "$STATE"
  helm_q upgrade --install skypiea-pred charts/skypiea --kube-context "$CTX" -n "$PRED" \
    --create-namespace -f charts/skypiea/values-kind.yaml --set imageTag="$TAG" \
    --set platform.enabled=true --set e0Probe.enabled=false \
    --set platform.predictor.serve=model --set platform.predictor.image.tag="$PRED_IMAGE_TAG" \
    --set platform.predictor.profile="$1" \
    --set-file platform.predictor.coldStartYaml=configs/cold_start.yaml \
    --set platform.gateway.turnHint=true \
    --set monitoring.enabled=true --set "monitoring.extraNamespaces={$BASE}" \
    --set monitoring.liveDashboard=true $(map_args) $la_args --wait --timeout 10m
  kubectl --context "$CTX" -n "$PRED" rollout status deploy/skypiea-model --timeout=180s >/dev/null
  echo "deployed $PRED (predictive: profile $1, lookahead $la, predictor $PRED_IMAGE_TAG," \
       "console mapping $([ "$LIVE_CONSOLE_MAP" = 1 ] && echo on || echo off), turn hint available)"
}

pf_stop() {
  for f in "$PFDIR"/*.pid; do
    if [ -e "$f" ]; then kill "$(cat "$f")" 2>/dev/null || true; rm -f "$f"; fi
  done
  return 0
}
fwd() {   # name ns svc local remote
  nohup kubectl --context "$CTX" -n "$2" port-forward --address 127.0.0.1 "svc/$3" "$4:$5" \
    >"$PFDIR/$1.log" 2>&1 &
  echo $! >"$PFDIR/$1.pid"
}
pf() {
  pf_stop
  fwd base-gw $BASE skypiea-gateway 18180 80
  fwd base-ctl $BASE skypiea-controller 18181 8081
  fwd base-stub $BASE skypiea-predictor 18182 80
  fwd pred-gw $PRED skypiea-gateway 18280 80
  fwd pred-ctl $PRED skypiea-controller 18281 8081
  fwd pred-stub $PRED skypiea-predictor 18282 80
  fwd pred-model $PRED skypiea-model 18283 80
  fwd grafana $PRED skypiea-grafana 3001 3000
  for _ in $(seq 1 30); do
    if curl -sf -m 2 http://127.0.0.1:18180/healthz >/dev/null && \
       curl -sf -m 2 http://127.0.0.1:18280/healthz >/dev/null && \
       curl -sf -m 2 http://127.0.0.1:18181/v1/state >/dev/null && \
       curl -sf -m 2 http://127.0.0.1:18281/v1/state >/dev/null; then
      echo "port-forwards up (127.0.0.1 only): baseline gateway :18180, predictive gateway :18280, Grafana :3001"
      return 0
    fi
    sleep 1
  done
  echo "port-forwards did not come up; see $PFDIR/*.log" >&2
  return 1
}

case "${1:-up}" in
up)
  deploy/kind/up.sh >/dev/null
  images
  prepull
  deploy_base
  deploy_pred "${2:-support}" "${LIVE_LOOKAHEAD:-1}"
  pf ;;
profile|lookahead)
  TAG=$(tools/image_tag.sh)
  [ -f "$STATE" ] && . "$STATE"        # keeps the DEPLOYED image tags (not the checkout's)
  if [ "$1" = profile ]; then PROFILE="${2:?support|coding}"; else LOOKAHEAD="${2:?1|2}"; fi
  if [ "${LOOKAHEAD:-1}" = 2 ] && [ "$PRED_IMAGE_TAG" = af000231bc7b ]; then
    echo "lookahead 2 needs the console predictor image (bundles in \$BUNDLES_DIR)" >&2; exit 1
  fi
  deploy_pred "${PROFILE:-support}" "${LOOKAHEAD:-1}"
  pf ;;
pf) pf ;;
pf-stop) pf_stop; echo "port-forwards stopped" ;;
down)
  pf_stop
  helm uninstall skypiea-pred --kube-context "$CTX" -n "$PRED" 2>/dev/null || true
  helm uninstall skypiea-base --kube-context "$CTX" -n "$BASE" 2>/dev/null || true
  kubectl --context "$CTX" delete ns "$PRED" "$BASE" --wait=false 2>/dev/null || true
  echo "both stacks removed" ;;
*) echo "usage: $0 up [support|coding] | profile support|coding | pf | pf-stop | down" >&2; exit 2 ;;
esac
