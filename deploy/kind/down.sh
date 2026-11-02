#!/usr/bin/env bash
# Delete the kind cluster. KEEP_REGISTRY=1 keeps the registry container (and its images).
set -euo pipefail
CLUSTER="${CLUSTER:-skypiea}"
REG_NAME="${REG_NAME:-kind-registry}"
kind delete cluster --name "${CLUSTER}"
if [ "${KEEP_REGISTRY:-0}" != "1" ]; then
  docker rm -f "${REG_NAME}" >/dev/null 2>&1 && echo "removed registry ${REG_NAME}" || true
fi
