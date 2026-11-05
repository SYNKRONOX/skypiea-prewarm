#!/usr/bin/env bash
# `make demo`: the whole Skypiea stack on kind from a fresh clone (after `make setup`).
#   1 kind cluster + local registry      (deploy/kind/up.sh, idempotent)
#   2 demo inputs from committed samples (python -m demo.prepare: SYNTHETIC prd bundle + sessions)
#   3 images: 16 tools, platform, predictor (prd demo bundle); built only if missing
#   4 push to localhost:5001
#   5 helm install, profile prd, gateway serving the stub (stand-in policies)
#   6 replay PRD Scenario A under scale_to_zero
#   7 switch the gateway to the real predictor (serve=model), replay under predictive
#   8 table: cold rate, added p50/p99, idle pod-seconds
# The cluster stays up (CLI, Grafana). `make demo-clean` removes it.
# FUNCTIONAL DEMO, NOT RESULTS: shortened gaps, kind cold starts, SYNTHETIC sessions.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PY:-.venv/bin/python}"
CTX="${KIND_CTX:-kind-skypiea}"
NS="${NS:-skypiea-tools}"
REG="${KIND_REG:-localhost:5001}"
SESSIONS="${DEMO_SESSIONS:-8}"
CONC="${DEMO_CONCURRENCY:-4}"
OUT=.demo
mkdir -p "$OUT"
T0=$(date +%s)
step() { echo; echo "=== [$(( $(date +%s) - T0 ))s] $*"; }

[ -x "$PY" ] || { echo "no $PY: run 'make setup' first" >&2; exit 1; }
for b in docker kind kubectl helm; do
  command -v "$b" >/dev/null || { echo "missing $b (see docs/DEMO.md, Prerequisites)" >&2; exit 1; }
done
docker info >/dev/null 2>&1 || { echo "docker daemon not reachable" >&2; exit 1; }

step "1/8 kind cluster 'skypiea' (1 control-plane + 1 worker) + registry $REG"
deploy/kind/up.sh

step "2/8 demo inputs from data/samples (SYNTHETIC prd bundle + Scenario A sessions)"
"$PY" -m demo.prepare --sessions "$SESSIONS"
BUNDLE=$(cat "$OUT/bundle_dir")

TAG=$(tools/image_tag.sh)
PRED_TAG="prd-demo-$(sha256sum "$BUNDLE/MANIFEST.json" | cut -c1-12)"
TOOLS=$("$PY" -c 'import yaml;print(" ".join(yaml.safe_load(open("tools/tool_map.yaml"))["servers"]))')

step "3/8 images (tag $TAG; predictor $PRED_TAG); existing images are reused"
missing=""
for t in $TOOLS; do docker image inspect "skypiea/$t:$TAG" >/dev/null 2>&1 || missing="$missing $t"; done
if [ -n "$missing" ]; then
  TOOLS="$missing" WITH_PROBE=0 WITH_PLATFORM=0 TAG="$TAG" tools/build_images.sh
fi
if ! docker image inspect "skypiea/platform:$TAG" >/dev/null 2>&1; then
  echo "==> building skypiea/platform:$TAG"
  DOCKER_BUILDKIT=1 docker build --quiet -f deploy/platform/Dockerfile -t "skypiea/platform:$TAG" . >/dev/null
fi
if ! docker image inspect "skypiea/predictor:$PRED_TAG" >/dev/null 2>&1; then
  echo "==> building skypiea/predictor:$PRED_TAG (prd demo bundle only; profile prd)"
  # the image expects three bundle folders; the demo image holds only the prd bundle, and the
  # service refuses a bundle whose manifest names another profile, so coding/support fail loudly
  DOCKER_BUILDKIT=1 docker build --quiet -f services/predictor/Dockerfile \
    --build-arg CODING_DIR="$BUNDLE" --build-arg SUPPORT_DIR="$BUNDLE" --build-arg PRD_DIR="$BUNDLE" \
    -t "skypiea/predictor:$PRED_TAG" . >/dev/null
fi

step "4/8 push to $REG"
for i in $TOOLS platform; do
  docker tag "skypiea/$i:$TAG" "$REG/skypiea/$i:$TAG"
  docker push -q "$REG/skypiea/$i:$TAG" >/dev/null
done
docker tag "skypiea/predictor:$PRED_TAG" "$REG/skypiea/predictor:$PRED_TAG"
docker push -q "$REG/skypiea/predictor:$PRED_TAG" >/dev/null
echo "pushed $(echo $TOOLS | wc -w) tools, platform, predictor"

deploy() { SERVE=$1 PRED_TAG=$PRED_TAG TAG=$TAG demo/deploy.sh; }
replay() {   # policy [extra args]
  local pol=$1; shift
  "$PY" -m gateway.scenario_run --base http://127.0.0.1:18080,http://127.0.0.1:18081,http://127.0.0.1:18082,http://127.0.0.1:18083 \
    --sessions-json "$OUT/sessions.json" --scenario A --policies "$pol" --dataset SYNTHETIC \
    --concurrency "$CONC" "$@" 2>&1 | tee "$OUT/run_$pol.log" | grep -v '^ROW ' || true
  grep -q '^FAILURES: none' "$OUT/run_$pol.log" || { echo "!! $pol: failures (see $OUT/run_$pol.log)"; FAILED=1; }
}
FAILED=0

step "5/8 deploy (profile prd, gateway -> stub predictor)"
deploy stub
demo/portforward.sh start

step "6/8 replay PRD Scenario A ($SESSIONS SYNTHETIC sessions, $CONC at once) under scale_to_zero"
replay scale_to_zero

step "7/8 gateway -> real predictor (profile prd); replay under predictive"
deploy model
demo/portforward.sh start
replay predictive --profile prd

step "8/8 summary"
"$PY" -m demo.table "$OUT/run_scale_to_zero.log" "$OUT/run_predictive.log" | tee "$OUT/demo_table.md"
echo
echo "Done in $(( $(date +%s) - T0 ))s. Cluster left up. Port-forwards (127.0.0.1 only) are running:"
echo "  bin/skypiea --context $CTX status         # CLI"
echo "  Grafana  http://127.0.0.1:3000             (anonymous viewer)"
echo "  Gateway  http://127.0.0.1:18080/mcp        (make agent-demo uses it)"
echo "Remove everything: make demo-clean"
exit "$FAILED"
