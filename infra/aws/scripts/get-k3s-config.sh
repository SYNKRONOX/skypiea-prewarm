#!/usr/bin/env bash
# Fetch the k3s kubeconfig from the VM over SSH, point it at the VM's public IP and save it to
# ~/.kube/skypiea-aws (outside the repo, mode 600). Use it with --kubeconfig explicitly.
set -euo pipefail
cd "$(dirname "$0")/.."
KEY="${SSH_KEY:-$HOME/.ssh/skypiea-aws}"
OUT="${KUBECONFIG_OUT:-$HOME/.kube/skypiea-aws}"
IP=$(terraform output -raw instance_ip)
SSH=(ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -i "$KEY" "ubuntu@$IP")

echo "waiting for SSH and the k3s bootstrap on $IP ..."
for _ in $(seq 1 90); do
  if "${SSH[@]}" test -f /var/lib/skypiea-bootstrap-done 2>/dev/null; then break; fi
  sleep 10
done
"${SSH[@]}" test -f /var/lib/skypiea-bootstrap-done || { echo "bootstrap not finished; see /var/log/skypiea-bootstrap.log on the VM" >&2; exit 1; }

mkdir -p "$(dirname "$OUT")"
umask 077
"${SSH[@]}" sudo cat /etc/rancher/k3s/k3s.yaml | python3 -c '
import sys, yaml
ip = sys.argv[1]
cfg = yaml.safe_load(sys.stdin)
for c in cfg["clusters"]:
    c["name"] = "skypiea-aws"
    c["cluster"]["server"] = f"https://{ip}:6443"
    c["cluster"]["tls-server-name"] = "kubernetes"   # cert SAN; IP changes on stop/start
for u in cfg["users"]:
    u["name"] = "skypiea-aws"
for x in cfg["contexts"]:
    x["name"] = "skypiea-aws"
    x["context"].update(cluster="skypiea-aws", user="skypiea-aws")
cfg["current-context"] = "skypiea-aws"
yaml.safe_dump(cfg, sys.stdout)' "$IP" > "$OUT"
chmod 600 "$OUT"
echo "wrote $OUT"
kubectl --kubeconfig "$OUT" get nodes -o wide
