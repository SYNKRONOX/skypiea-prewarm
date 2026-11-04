#!/usr/bin/env bash
# Functional Scenario A/B replay on AWS as a Kubernetes Job INSIDE the cluster, so nothing on the
# laptop holds a connection while it runs (gateway/replay_check.py --in-cluster). Short calls only:
#   replay-job.sh start [POLICIES]   export sessions, upload Job + ConfigMap to S3, apply on the node
#   CHECK=concurrent N=40 replay-job.sh start [POLICIES]   N sessions at once (gateway/concurrent_check.py)
#   replay-job.sh status             job state + last log lines (one short SSM call)
#   replay-job.sh logs  OUT          full log to OUT (outside results/: functional, not results)
# The Job uses the platform image the gateway runs, with the CURRENT replay_check.py mounted from
# a ConfigMap. FUNCTIONAL CHECK ONLY: no latency it prints is a result.
set -euo pipefail
cd "$(dirname "$0")/../../.."
TF="terraform -chdir=infra/aws"
REGION="${AWS_REGION:-ap-south-1}"
NS="${NS:-skypiea-tools}"
PY="${PY:-.venv/bin/python}"
export SKYPIEA_INSTANCE_ID=$($TF output -raw instance_id)
BUCKET=$($TF output -raw bucket_name)
NODE_RUN=infra/aws/scripts/node-run.sh
case "${1:-}" in
start)
  CHECK="${CHECK:-replay}"
  T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
  if [ "$CHECK" = concurrent ]; then
    POL="${2:-scale_to_zero,markov1}"
    "$PY" -m gateway.concurrent_check --export-sessions "$T/sessions.json" --n "${N:-40}"
  else
    POL="${2:-scale_to_zero,always_warm,keep_alive,markov1}"
    "$PY" -m gateway.replay_check --export-sessions "$T/sessions.json" >/dev/null
  fi
  "$PY" - "$T" "$NS" "$POL" "$CHECK" <<'PY'
import json, pathlib, sys
t, ns, pol, check = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
script = {"replay": "gateway/replay_check.py", "concurrent": "gateway/concurrent_check.py"}[check]
extra = ["--in-cluster"] if check == "replay" else []
lab = {"app.kubernetes.io/part-of": "skypiea", "app.kubernetes.io/name": "skypiea-replay"}
cm = {"apiVersion": "v1", "kind": "ConfigMap",
      "metadata": {"name": "skypiea-replay", "namespace": ns, "labels": lab},
      "data": {"check.py": pathlib.Path(script).read_text(),
               "sessions.json": (t / "sessions.json").read_text()}}
job = {"apiVersion": "batch/v1", "kind": "Job",
       "metadata": {"name": "skypiea-replay", "namespace": ns, "labels": lab},
       "spec": {"backoffLimit": 0, "activeDeadlineSeconds": 2700, "template": {
           "metadata": {"labels": lab},
           "spec": {"restartPolicy": "Never", "automountServiceAccountToken": False,
                    "enableServiceLinks": False,
                    "imagePullSecrets": [{"name": "ecr-pull"}],
                    "securityContext": {"runAsNonRoot": True, "runAsUser": 10001,
                                        "seccompProfile": {"type": "RuntimeDefault"}},
                    "containers": [{
                        "name": "replay", "image": "@IMAGE@", "imagePullPolicy": "IfNotPresent",
                        "command": ["python", "/work/check.py", *extra,
                                    "--namespace", ns, "--sessions-json", "/work/sessions.json",
                                    "--policies", pol],
                        "env": [{"name": "PYTHONUNBUFFERED", "value": "1"}],
                        "resources": {"requests": {"cpu": "50m", "memory": "64Mi"},
                                      "limits": {"cpu": "500m", "memory": "128Mi"}},
                        "securityContext": {"allowPrivilegeEscalation": False,
                                            "readOnlyRootFilesystem": True,
                                            "capabilities": {"drop": ["ALL"]}},
                        "volumeMounts": [{"name": "work", "mountPath": "/work"}]}],
                    "volumes": [{"name": "work", "configMap": {"name": "skypiea-replay"}}]}}}}
(t / "replay.json").write_text(json.dumps({"apiVersion": "v1", "kind": "List",
                                           "items": [cm, job]}))
PY
  KEY="replay/$(date -u +%Y%m%dT%H%M%SZ)/replay.json"
  aws s3 cp --region "$REGION" --only-show-errors "$T/replay.json" "s3://$BUCKET/$KEY"
  "$NODE_RUN" "set -e
K='k3s kubectl -n $NS'
\$K delete job skypiea-replay --ignore-not-found --wait=true >/dev/null
IMG=\$(\$K get deploy skypiea-gateway -o jsonpath='{.spec.template.spec.containers[0].image}')
/snap/bin/aws s3 cp --region $REGION --only-show-errors s3://$BUCKET/$KEY /tmp/replay.json
sed \"s|@IMAGE@|\$IMG|\" /tmp/replay.json | \$K apply -f - ; rm -f /tmp/replay.json
echo \"image: \${IMG##*/}\"" | sed -E 's/[0-9]{12}(\.dkr)/<account>\1/g'
  ;;
status)
  "$NODE_RUN" "k3s kubectl -n $NS get job skypiea-replay --no-headers; k3s kubectl -n $NS logs job/skypiea-replay --tail=${TAIL:-6} 2>&1"
  ;;
logs)
  "$NODE_RUN" "k3s kubectl -n $NS logs job/skypiea-replay 2>&1" > "${2:?out file}"
  wc -l "$2"
  ;;
*) sed -n 2,8p "$0"; exit 2 ;;
esac
