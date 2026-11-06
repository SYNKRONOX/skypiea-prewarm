#!/usr/bin/env bash
# `make live`: both stacks + the Live Console, then print the URLs.
#   LIVE_DEMO=support|coding (default support)  AGENT_BASE_URL / AGENT_MODEL (default Ollama)
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PY:-.venv/bin/python}"
[ -x "$PY" ] || { echo "no $PY: run 'make setup' first" >&2; exit 1; }
DOCTOR_QUIET=1 tools/doctor.sh live || { echo "make live stopped before changing anything: fix the FAIL lines above (make doctor shows all checks)" >&2; exit 1; }
CONSOLE_PORT="${CONSOLE_PORT:-8090}"
ours=0
if [ -f .live/console.pid ] && kill -0 "$(cat .live/console.pid)" 2>/dev/null; then ours=1; fi
if [ $ours = 0 ] && curl -s -m 2 -o /dev/null "http://127.0.0.1:$CONSOLE_PORT/" 2>/dev/null; then
  echo "make live: port $CONSOLE_PORT is already in use by another program (another make live from a" >&2
  echo "different folder?). Stop it first (ss -ltnp | grep :$CONSOLE_PORT); nothing was changed." >&2
  exit 1
fi
export AGENT_BASE_URL="${AGENT_BASE_URL:-http://localhost:11434/v1}"
export AGENT_MODEL="${AGENT_MODEL:-llama3.1:8b}"
T0=$(date +%s)
demo/live_stacks.sh up "${LIVE_DEMO:-support}"
mkdir -p .live
if [ -f .live/console.pid ] && kill -0 "$(cat .live/console.pid)" 2>/dev/null; then
  kill "$(cat .live/console.pid)"; sleep 1
fi
nohup "$PY" -m console >.live/console.log 2>&1 &
echo $! >.live/console.pid
for _ in $(seq 1 40); do curl -sf -m 2 http://127.0.0.1:$CONSOLE_PORT/api/state >/dev/null && break; sleep 0.5; done
curl -sf -m 2 http://127.0.0.1:$CONSOLE_PORT/api/state >/dev/null || { cat .live/console.log; exit 1; }
if curl -sf -m 5 "${AGENT_BASE_URL%/}/models" >/dev/null; then llm="reachable"; else
  llm="NOT reachable (docs/DEMO.md, Troubleshooting: Ollama)"; fi
cat <<EOF

Skypiea Live Console is up ($(( $(date +%s) - T0 )) s):
  Console   http://127.0.0.1:$CONSOLE_PORT                 (open in a browser; projector: zoom 125-150%)
  Grafana   http://127.0.0.1:3001                 dashboard "Skypiea Live: baseline vs predictive"
  LLM       $AGENT_MODEL at $AGENT_BASE_URL: $llm
  Logs      .live/console.log, port-forwards .live/pf/
Stop everything: make live-clean   (the kind cluster and registry stay; make clean-cluster removes them)
EOF
