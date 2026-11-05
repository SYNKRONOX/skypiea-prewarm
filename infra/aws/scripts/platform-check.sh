#!/usr/bin/env bash
# Metrics + Grafana check on AWS in ONE short SSM call (platform_check_node.py on the node).
set -euo pipefail
cd "$(dirname "$0")"
export SKYPIEA_INSTANCE_ID=$(terraform -chdir=.. output -raw instance_id)
B64=$(base64 -w0 platform_check_node.py)
./node-run.sh "echo $B64 | base64 -d > /tmp/pc.py && python3 /tmp/pc.py ${NS:-skypiea-tools}; rc=\$?; rm -f /tmp/pc.py; exit \$rc"
