#!/usr/bin/env bash
# Loopback-only port-forwards to the platform Services on kind (used by `make demo` and
# `make agent-demo`). Nothing is exposed beyond 127.0.0.1.
#   demo/portforward.sh start   gateway :18080, controller :18081, stub predictor :18082,
#                               model predictor :18083 (if deployed), Grafana :3000
#                               (PF_BASE moves the four 1808x ports, GRAFANA_PORT moves Grafana)
#   demo/portforward.sh stop
#   demo/portforward.sh check   exit 0 if the gateway answers on :18080
set -euo pipefail
cd "$(dirname "$0")/.."
CTX="${KIND_CTX:-kind-${CLUSTER:-skypiea}}"
NS="${NS:-skypiea-tools}"
DIR=.demo/pf
mkdir -p "$DIR"
PF_BASE="${PF_BASE:-18080}"           # gateway; controller +1, stub +2, model +3
GW_PORT="${GW_PORT:-$PF_BASE}"
CTL_PORT=$((PF_BASE + 1)); STUB_PORT=$((PF_BASE + 2)); MODEL_PORT=$((PF_BASE + 3))
GRAFANA_PORT="${GRAFANA_PORT:-3000}"

stop() {
  for f in "$DIR"/*.pid; do
    [ -e "$f" ] || continue
    kill "$(cat "$f")" 2>/dev/null || true
    rm -f "$f"
  done
}

fwd() {   # name svc local remote
  if ! kubectl --context "$CTX" -n "$NS" get svc "$2" >/dev/null 2>&1; then
    echo "  (no svc $2: skipped)"; return 0
  fi
  nohup kubectl --context "$CTX" -n "$NS" port-forward --address 127.0.0.1 "svc/$2" "$3:$4" \
    >"$DIR/$1.log" 2>&1 &
  echo $! >"$DIR/$1.pid"
}

check() { curl -sf -m 3 "http://127.0.0.1:${GW_PORT}/healthz" >/dev/null; }

case "${1:-start}" in
start)
  stop
  fwd gateway skypiea-gateway "$GW_PORT" 80
  fwd controller skypiea-controller "$CTL_PORT" 8081
  fwd stub skypiea-predictor "$STUB_PORT" 80
  fwd model skypiea-model "$MODEL_PORT" 80
  if [ "${GRAFANA:-1}" = 1 ]; then fwd grafana skypiea-grafana "$GRAFANA_PORT" 3000; fi
  for _ in $(seq 1 30); do
    if check && curl -sf -m 3 "http://127.0.0.1:$CTL_PORT/v1/state" >/dev/null; then
      echo "port-forwards up: gateway http://127.0.0.1:${GW_PORT}/mcp, controller :$CTL_PORT," \
           "stub :$STUB_PORT, model :$MODEL_PORT, Grafana http://127.0.0.1:$GRAFANA_PORT"
      exit 0
    fi
    sleep 1
  done
  echo "port-forwards did not come up; see $DIR/*.log" >&2
  if grep -qs 'address already in use' "$DIR"/*.log; then
    echo "a local port is taken by another program: $(grep -ho '127.0.0.1:[0-9]*' "$DIR"/*.log | sort -u | tr '\n' ' ')" >&2
    echo "free it, or use other ports: PF_BASE=28080 GRAFANA_PORT=3300 make demo" >&2
  fi
  exit 1 ;;
stop) stop; echo "port-forwards stopped" ;;
check) check ;;
*) echo "usage: $0 start|stop|check" >&2; exit 2 ;;
esac
