#!/usr/bin/env bash
# helm upgrade --install of the chart on kind, profile prd, the gateway serving SERVE.
#   SERVE=stub|model PRED_TAG=<predictor image tag> [TAG=<tools/platform tag>] demo/deploy.sh
set -euo pipefail
cd "$(dirname "$0")/.."
CTX="${KIND_CTX:-kind-skypiea}"
NS="${NS:-skypiea-tools}"
TAG="${TAG:-$(tools/image_tag.sh)}"
: "${SERVE:?SERVE=stub|model}" "${PRED_TAG:?PRED_TAG=<predictor image tag>}"
helm upgrade --install skypiea charts/skypiea --kube-context "$CTX" -n "$NS" --create-namespace \
  -f charts/skypiea/values-kind.yaml --set imageTag="$TAG" \
  --set platform.enabled=true --set e0Probe.enabled=false \
  --set platform.predictor.image.tag="$PRED_TAG" --set platform.predictor.profile=prd \
  --set-file platform.predictor.coldStartYaml=configs/cold_start.yaml \
  --set platform.predictor.serve="$SERVE" --wait --timeout 10m 2>&1 >/dev/null \
  | grep -v 'client-side throttling' || true
helm status skypiea --kube-context "$CTX" -n "$NS" -o json | grep -q '"status":"deployed"' \
  || { echo "helm release not deployed" >&2; exit 1; }
kubectl --context "$CTX" -n "$NS" rollout status deploy/skypiea-gateway --timeout=180s >/dev/null
kubectl --context "$CTX" -n "$NS" rollout status deploy/skypiea-model --timeout=180s >/dev/null
echo "deployed: images $TAG, predictor $PRED_TAG (profile prd), gateway serves the $SERVE"
