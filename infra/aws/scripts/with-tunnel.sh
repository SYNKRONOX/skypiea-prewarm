#!/usr/bin/env bash
# Run ONE short command with the k3s API tunnel up, then tear the tunnel down (never left open).
#   infra/aws/scripts/with-tunnel.sh helm upgrade ...      (hard cap: TUNNEL_MAX_S, default 180 s)
set -euo pipefail
cd "$(dirname "$0")"
PORT="${LOCAL_PORT:-16443}"
MAX="${TUNNEL_MAX_S:-180}"
timeout "$MAX" ./tunnel.sh < <(sleep "$MAX") >/dev/null 2>&1 &
TP=$!
trap 'kill $TP 2>/dev/null || true; pkill -P $TP 2>/dev/null || true' EXIT
for _ in $(seq 1 60); do
  (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null && break
  sleep 0.5
done
cd - >/dev/null
timeout "$((MAX - 10))" "$@"
