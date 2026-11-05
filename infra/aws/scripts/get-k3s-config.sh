#!/usr/bin/env bash
# Fetch the k3s kubeconfig from the VM over SSM (no SSH), point it at the local end of the SSM
# port-forward (https://127.0.0.1:${LOCAL_PORT}, see tunnel.sh) and save it outside the repo
# (~/.kube/skypiea-aws, mode 600). The VM's public IP never appears in it.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
OUT="${KUBECONFIG_OUT:-$HOME/.kube/skypiea-aws}"
LOCAL_PORT="${LOCAL_PORT:-16443}"

echo "waiting for the SSM agent and the k3s bootstrap ..."
for _ in $(seq 1 60); do
  if "$here/node-run.sh" 'test -f /var/lib/skypiea-bootstrap-done' >/dev/null 2>&1; then break; fi
  sleep 10
done
"$here/node-run.sh" 'test -f /var/lib/skypiea-bootstrap-done' || {
  echo "bootstrap not finished; see: make aws-shell, then /var/log/skypiea-bootstrap.log" >&2; exit 1; }

mkdir -p "$(dirname "$OUT")"
umask 077
"$here/node-run.sh" 'cat /etc/rancher/k3s/k3s.yaml' | python3 -c '
import sys, yaml
port = sys.argv[1]
cfg = yaml.safe_load(sys.stdin)
for c in cfg["clusters"]:
    c["name"] = "skypiea-aws"
    c["cluster"]["server"] = f"https://127.0.0.1:{port}"   # 127.0.0.1 is in the k3s cert SANs
for u in cfg["users"]:
    u["name"] = "skypiea-aws"
for x in cfg["contexts"]:
    x["name"] = "skypiea-aws"
    x["context"].update(cluster="skypiea-aws", user="skypiea-aws")
cfg["current-context"] = "skypiea-aws"
yaml.safe_dump(cfg, sys.stdout)' "$LOCAL_PORT" > "$OUT"
chmod 600 "$OUT"
echo "wrote $OUT (server https://127.0.0.1:$LOCAL_PORT; needs: make aws-tunnel)"
