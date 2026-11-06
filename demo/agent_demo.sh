#!/usr/bin/env bash
# `make agent-demo [LLM=scripted|openai-compatible] [POLICY=predictive] [N=3]`
# Runs the agent on Scenario A and B against the kind stack left up by `make demo`.
#   LLM=scripted           ScriptedLLM (fixed tool order, 3 s fake think time): no LLM network
#   LLM=openai-compatible  AGENT_BASE_URL, AGENT_MODEL (and optional AGENT_API_KEY) from the env
# POLICY=predictive needs the gateway serving the model (as `make demo` leaves it);
# scale_to_zero / keep_alive need it serving the stub (see docs/AGENT.md).
# Output: .demo/agent_runs/ (functional demo, not results).
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PY:-.venv/bin/python}"
LLM="${LLM:-scripted}"
POLICY="${POLICY:-predictive}"
N="${N:-3}"
OUT="${OUT:-.demo/agent_runs}"
PF_BASE="${PF_BASE:-18080}"
URLS=(--gateway "http://127.0.0.1:$PF_BASE/mcp" --controller "http://127.0.0.1:$((PF_BASE + 1))"
      --stub "http://127.0.0.1:$((PF_BASE + 2))" --model-url "http://127.0.0.1:$((PF_BASE + 3))")
demo/portforward.sh check || demo/portforward.sh start
if [ "$LLM" = openai-compatible ]; then
  : "${AGENT_BASE_URL:?set AGENT_BASE_URL, e.g. http://localhost:11434/v1}"
  : "${AGENT_MODEL:?set AGENT_MODEL, e.g. llama3.1:8b}"
  curl -sf -m 10 "${AGENT_BASE_URL%/}/models" >/dev/null || {
    echo "LLM endpoint ${AGENT_BASE_URL%/}/models not reachable (docs/AGENT.md, Troubleshooting)" >&2
    exit 1; }
fi
rc=0
for S in A B; do
  "$PY" -m agent.run --scenario "$S" --policy "$POLICY" --llm "$LLM" --sessions "$N" \
    --out-dir "$OUT" "${URLS[@]}" || rc=1
done
"$PY" -m agent.summarize --dir "$OUT" --llm "$LLM" >/dev/null && \
  echo "summary: $OUT/summary.md (functional demo, not results)"
exit $rc
