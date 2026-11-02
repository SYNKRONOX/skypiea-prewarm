#!/usr/bin/env bash
# Create the kind cluster "skypiea" + local registry localhost:5001 (official kind recipe:
# https://kind.sigs.k8s.io/docs/user/local-registry/). Idempotent.
set -euo pipefail
cd "$(dirname "$0")"

CLUSTER="${CLUSTER:-skypiea}"
REG_NAME="${REG_NAME:-kind-registry}"
REG_PORT="${REG_PORT:-5001}"
REG_IMAGE="registry:3"

# 1. Registry container
if [ "$(docker inspect -f '{{.State.Running}}' "${REG_NAME}" 2>/dev/null || true)" != 'true' ]; then
  docker rm -f "${REG_NAME}" >/dev/null 2>&1 || true
  docker run -d --restart=always -p "127.0.0.1:${REG_PORT}:5000" --network bridge \
    --name "${REG_NAME}" "${REG_IMAGE}"
fi

# 2. Cluster
if ! kind get clusters | grep -qx "${CLUSTER}"; then
  kind create cluster --name "${CLUSTER}" --config cluster.yaml --wait 120s
fi

# 3. Tell containerd on every node that localhost:5001 is the registry container
REGISTRY_DIR="/etc/containerd/certs.d/localhost:${REG_PORT}"
for node in $(kind get nodes --name "${CLUSTER}"); do
  docker exec "${node}" mkdir -p "${REGISTRY_DIR}"
  printf '[host."http://%s:5000"]\n' "${REG_NAME}" | \
    docker exec -i "${node}" cp /dev/stdin "${REGISTRY_DIR}/hosts.toml"
done

# 4. Registry on the kind network
if [ "$(docker inspect -f='{{json .NetworkSettings.Networks.kind}}' "${REG_NAME}")" = 'null' ]; then
  docker network connect kind "${REG_NAME}"
fi

# 5. Document the local registry (KEP-1755)
kubectl --context "kind-${CLUSTER}" apply -f - <<YAML
apiVersion: v1
kind: ConfigMap
metadata:
  name: local-registry-hosting
  namespace: kube-public
data:
  localRegistryHosting.v1: |
    host: "localhost:${REG_PORT}"
    help: "https://kind.sigs.k8s.io/docs/user/local-registry/"
YAML

kubectl --context "kind-${CLUSTER}" get nodes -o wide
