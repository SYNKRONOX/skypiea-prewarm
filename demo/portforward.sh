#!/usr/bin/env bash
# Loopback-only port-forwards to the platform Services on kind (used by `make demo` and
# `make agent-demo`). Nothing is exposed beyond 127.0.0.1.
#   demo/portforward.sh start   gateway :18080, controller :18081, stub predictor :18082,
#                               model predictor :18083 (if deployed), Grafana :3000
#   demo/portforward.sh stop
#   demo/portforward.sh check   exit 0 if the gateway answers on :18080
set -euo pipefail
cd "$(dirname "$0")/.."
CTX="${KIND_CTX:-kind-skypiea}"
NS="${NS:-skypiea-tools}"
DIR=.demo/pf
mkdir -p "$DIR"
GW_PORT="${GW_PORT:-18080}"

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
  fwd controller skypiea-controller 18081 8081
  fwd stub skypiea-predictor 18082 80
  fwd model skypiea-model 18083 80
  if [ "${GRAFANA:-1}" = 1 ]; then fwd grafana skypiea-grafana 3000 3000; fi
  for _ in $(seq 1 30); do
    if check && curl -sf -m 3 http://127.0.0.1:18081/v1/state >/dev/null; then
      echo "port-forwards up: gateway http://127.0.0.1:${GW_PORT}/mcp, controller :18081," \
           "stub :18082, model :18083, Grafana http://127.0.0.1:3000"
      exit 0
    fi
    sleep 1
  done
  echo "port-forwards did not come up; see $DIR/*.log" >&2
  exit 1 ;;
stop) stop; echo "port-forwards stopped" ;;
check) check ;;
*) echo "usage: $0 start|stop|check" >&2; exit 2 ;;
esac
