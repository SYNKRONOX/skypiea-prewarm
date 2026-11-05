#!/usr/bin/env bash
# SSM port-forward: laptop 127.0.0.1:LOCAL_PORT -> VM 127.0.0.1:6443 (k3s API). Runs in the
# foreground; keep it open in its own terminal. Listens on the laptop's loopback only.
# Everything else (gateway demo, Grafana) then goes through kubectl port-forward over it.
set -euo pipefail
cd "$(dirname "$0")/.."
ID="${SKYPIEA_INSTANCE_ID:-$(terraform output -raw instance_id)}"
LOCAL_PORT="${LOCAL_PORT:-16443}"
exec aws ssm start-session --region "${AWS_REGION:-ap-south-1}" --target "$ID" \
  --document-name AWS-StartPortForwardingSession \
  --parameters "{\"portNumber\":[\"6443\"],\"localPortNumber\":[\"$LOCAL_PORT\"]}"
