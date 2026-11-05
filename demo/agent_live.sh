#!/usr/bin/env bash
# STEP 2 live runs (`make agent-live`): the agent with a live LLM on kind, profile prd
# (SYNTHETIC), real (unscaled) LLM think times, N sessions per scenario x policy:
#   scale_to_zero, keep_alive (30 s)  -> gateway serves the stub
#   predictive                        -> gateway serves the model
# Every run waits until all tools are back at 0 replicas before and after it.
# Records everything into results/agent_runs/ (no tuning, no reruns), then summarises.
#   AGENT_BASE_URL (default http://localhost:11434/v1), AGENT_MODEL (default llama3.1:8b)
#   PRED_TAG: predictor image (default: the shipped prd bundle a7642d03dd10 if the local
#   registry has it, else the demo bundle image of `make demo`)
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PY:-.venv/bin/python}"
N="${N:-10}"
OUT="${OUT:-results/agent_runs}"
export AGENT_BASE_URL="${AGENT_BASE_URL:-http://localhost:11434/v1}"
export AGENT_MODEL="${AGENT_MODEL:-llama3.1:8b}"
REG="${KIND_REG:-localhost:5001}"
if [ -z "${PRED_TAG:-}" ]; then
  tags=$(curl -sf "http://$REG/v2/skypiea/predictor/tags/list" || true)
  if echo "$tags" | grep -q '"a7642d03dd10"'; then PRED_TAG=a7642d03dd10
  else PRED_TAG=$(echo "$tags" | grep -o 'prd-demo-[0-9a-f]*' | tail -1); fi
fi
[ -n "$PRED_TAG" ] || { echo "no prd predictor image in $REG: run make demo first" >&2; exit 1; }
curl -sf -m 10 "${AGENT_BASE_URL%/}/models" >/dev/null \
  || { echo "LLM endpoint ${AGENT_BASE_URL%/}/models not reachable" >&2; exit 1; }
mkdir -p "$OUT"
echo "[live] $(date -u +%FT%TZ) model $AGENT_MODEL at $AGENT_BASE_URL, predictor $PRED_TAG, N=$N"
rc=0
run() { "$PY" -m agent.run --llm openai-compatible --sessions "$N" --out-dir "$OUT" "$@" || rc=1; }

SERVE=stub PRED_TAG=$PRED_TAG demo/deploy.sh
demo/portforward.sh start
for POL in scale_to_zero keep_alive; do
  for S in A B; do run --scenario "$S" --policy "$POL"; done
done
SERVE=model PRED_TAG=$PRED_TAG demo/deploy.sh
demo/portforward.sh start
for S in A B; do run --scenario "$S" --policy predictive; done
"$PY" -m agent.summarize --dir "$OUT" --llm openai-compatible
echo "[live] $(date -u +%FT%TZ) done rc=$rc"
exit $rc
