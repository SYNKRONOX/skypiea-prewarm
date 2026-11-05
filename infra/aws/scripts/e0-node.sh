#!/usr/bin/env bash
# Drive E0 ON the k3s node from the laptop with short calls only (each SSM call < 60 s; no
# tunnel, no long exec). The job itself is the systemd unit skypiea-e0 on the node
# (e0-node-job.sh): it measures, uploads to S3 with verification and then stops the instance.
#   e0-node.sh start            export the committed harness, upload it to S3, start the unit
#   e0-node.sh status [RUN_ID]  done/total, ETA, last error (from the node, or from S3 if stopped)
#   e0-node.sh fetch  [RUN_ID]  download from S3, verify size+sha256, mask the account id,
#                               write results/e0_aws*_runs.csv and re-make the reports
set -euo pipefail
cd "$(dirname "$0")/../../.."
TF="terraform -chdir=infra/aws"
REGION="${AWS_REGION:-ap-south-1}"
PY="${PY:-.venv/bin/python}"
STATE_DIR="$HOME/.cache/skypiea/e0-aws"        # outside the repo: raw files carry the account id
mkdir -p "$STATE_DIR"
NODE_RUN=infra/aws/scripts/node-run.sh
ID=$($TF output -raw instance_id)
BUCKET=$($TF output -raw bucket_name)
export SKYPIEA_INSTANCE_ID="$ID"
run_id() { echo "${1:-$(cat "$STATE_DIR/last_run_id")}"; }
inst_state() { aws ec2 describe-instances --region "$REGION" --instance-ids "$ID" \
                 --query 'Reservations[0].Instances[0].State.Name' --output text; }

case "${1:-}" in
start)
  N="${N:-10}"
  IMAGE_TAG="${IMAGE_TAG:?set IMAGE_TAG (the deployed tag)}"
  if [ -n "$(git status --porcelain -- experiments/ tools/tool_map.yaml infra/aws/)" ]; then
    echo "commit experiments/, tools/tool_map.yaml and infra/aws/ first (the run records HEAD)" >&2
    exit 1
  fi
  [ "$(inst_state)" = running ] || { echo "instance is not running (make aws-start)" >&2; exit 1; }
  COMMIT=$(git rev-parse --short HEAD)
  RUN_ID="e0-$(date -u +%Y%m%dT%H%M%SZ)-$COMMIT"
  B=$(mktemp -d)
  trap 'rm -rf "$B"' EXIT
  git archive HEAD experiments/__init__.py experiments/e0_cold_start.py \
    experiments/e0_config.yaml tools/tool_map.yaml infra/aws/scripts/e0-node-job.sh \
    infra/aws/e0-node.service | tar -x -C "$B"
  printf 'RUN_ID=%s\nBUCKET=%s\nREGION=%s\nGIT_COMMIT=%s\nN=%s\n' \
    "$RUN_ID" "$BUCKET" "$REGION" "$COMMIT" "$N" > "$B/job.env"
  # image_bytes_local as on kind: size of the laptop's image (the node has no docker)
  "$PY" - "$IMAGE_TAG" > "$B/image_sizes.json" <<'PY'
import json, subprocess, sys, yaml
tools = yaml.safe_load(open("tools/tool_map.yaml"))["servers"]
out = {}
for t in tools:
    p = subprocess.run(["docker", "image", "inspect", f"skypiea/{t}:{sys.argv[1]}",
                        "--format", "{{.Size}}"], capture_output=True, text=True)
    out[t] = int(p.stdout) if p.returncode == 0 else None
print(json.dumps(out))
PY
  tar -czf "$B/bundle.tgz" -C "$B" experiments tools infra job.env image_sizes.json
  aws s3 cp --region "$REGION" --only-show-errors "$B/bundle.tgz" "s3://$BUCKET/e0/$RUN_ID/bundle.tgz"
  "$NODE_RUN" "set -e
systemctl is-active --quiet skypiea-e0 && { echo 'skypiea-e0 is already running'; exit 4; }
D=/opt/skypiea-e0/$RUN_ID; mkdir -p \$D
/snap/bin/aws s3 cp --region $REGION --only-show-errors s3://$BUCKET/e0/$RUN_ID/bundle.tgz \$D/bundle.tgz
tar -xzf \$D/bundle.tgz -C \$D
sed -e \"s|@DIR@|\$D|g\" -e 's|@RUN_ID@|$RUN_ID|g' \$D/infra/aws/e0-node.service > /etc/systemd/system/skypiea-e0.service
systemctl daemon-reload; systemctl reset-failed skypiea-e0 2>/dev/null || true
systemctl start skypiea-e0; sleep 3; echo \"unit: \$(systemctl is-active skypiea-e0)\"; tail -n 3 \$D/job.log"
  echo "$RUN_ID" > "$STATE_DIR/last_run_id"
  echo "started $RUN_ID (status: make e0-aws-status)"
  ;;
status)
  RUN_ID=$(run_id "${2:-}")
  st=$(inst_state)
  echo "run $RUN_ID; instance $st"
  if [ "$st" = running ]; then
    "$NODE_RUN" "D=/opt/skypiea-e0/$RUN_ID
echo \"unit: \$(systemctl is-active skypiea-e0) (restarts: \$(systemctl show -p NRestarts --value skypiea-e0))\"
for f in \$D/results/*_progress.json; do [ -f \$f ] && python3 -c 'import json,sys
p=json.load(open(sys.argv[1]))
print(f\"{p[\"stem\"]}: {p[\"units_done\"]}/{p[\"units_total\"]} units ({p[\"state\"]}), ok {p[\"units_ok\"]}, given up {p[\"units_given_up\"]}, error rows {p[\"error_rows\"]}, ETA {p[\"eta_min\"]} min, updated {p[\"updated_at\"]}\")
print(f\"  last error: {p[\"last_error\"]} at {p[\"last_error_at\"]}\") if p[\"last_error\"] else None' \$f; done
tail -n 3 \$D/results/job_state 2>/dev/null; tail -n 2 \$D/job.log" | sed -E 's/[0-9]{12}(\.dkr)/<account>\1/g'
  else
    aws s3 cp --region "$REGION" --only-show-errors "s3://$BUCKET/e0/$RUN_ID/job_state" - 2>/dev/null \
      || echo "no job_state in S3 yet"
  fi
  ;;
fetch)
  RUN_ID=$(run_id "${2:-}")
  RAW="$STATE_DIR/$RUN_ID"
  mkdir -p "$RAW"
  aws s3 cp --region "$REGION" --only-show-errors --recursive --exclude bundle.tgz \
    "s3://$BUCKET/e0/$RUN_ID/" "$RAW/"
  SKYPIEA_ACCT=$(aws sts get-caller-identity --query Account --output text) "$PY" - "$RAW" <<'PY'
import hashlib, json, os, pathlib, re, sys
raw = pathlib.Path(sys.argv[1])
man = json.loads((raw / "manifest.json").read_text())
for name, m in man.items():
    b = (raw / name).read_bytes()
    assert len(b) == m["bytes"], f"{name}: size {len(b)} != {m['bytes']}"
    assert hashlib.sha256(b).hexdigest() == m["sha256"], f"{name}: sha256 mismatch"
    assert len(b) < 1_000_000, f"{name}: {len(b)} B is not < 1 MB"
print(f"verified {len(man)} files against manifest.json (size + sha256), all < 1 MB")
res = pathlib.Path("results")
mask = lambda s: re.sub(r"(?<=:)\d{12}(?=:)", "<account>",
                        re.sub(r"\d{12}(?=\.dkr\.ecr)", "<account>", s))
for src, dst in [("e0_aws_runs.csv", "e0_aws_runs.csv"),
                 ("e0_aws_p3_runs.csv", "e0_aws_p3_runs.csv"),
                 ("run_params.json", "e0_aws_run_params.json"),
                 ("job.log", "e0_aws_job.log"), ("job_state.txt", "e0_aws_job_state.txt")]:
    t = mask((raw / src).read_text())
    # exact check against the real account id (env only, never printed); a generic 12-digit
    # search hits floats such as 8007.999999999999
    acct = os.environ["SKYPIEA_ACCT"]
    assert len(acct) == 12 and acct not in t, f"{src}: account id left after masking"
    assert not re.search(r"\d{12}\.dkr\.ecr|:\d{12}:", t), f"{src}: unmasked ECR/ARN id"
    (res / dst).write_text(t)
    print(f"wrote results/{dst} ({len(t)} B, account id masked)")
PY
  "$PY" -m experiments.e0_cold_start --target aws --report-only
  "$PY" -m experiments.e0_cold_start --target aws --parallel 3 --report-only
  ;;
*) sed -n 2,9p "$0"; exit 2 ;;
esac
