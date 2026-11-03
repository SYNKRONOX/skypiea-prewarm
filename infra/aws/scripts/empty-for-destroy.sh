#!/usr/bin/env bash
# Empty the Skypiea S3 bucket (all objects, all versions, delete markers) and every
# skypiea/* ECR repository, so `terraform destroy` cannot stop half-way on a non-empty bucket.
# Reads names from Terraform outputs; touches nothing else. Prints counts only.
set -euo pipefail
cd "$(dirname "$0")/.."
REGION="${AWS_REGION:-ap-south-1}"
BUCKET=$(terraform output -raw bucket_name 2>/dev/null || true)
REPOS=$(terraform output -json ecr_repository_urls 2>/dev/null | python3 -c '
import json, sys
for url in json.load(sys.stdin).values():
    print(url.split("/", 1)[1])' || true)

if [ -n "$BUCKET" ]; then
  n=0
  while :; do
    batch=$(aws s3api list-object-versions --region "$REGION" --bucket "$BUCKET" --max-items 1000 \
      --output json | python3 -c '
import json, sys
d = json.loads(sys.stdin.read() or "{}")
objs = [{"Key": o["Key"], "VersionId": o["VersionId"]}
        for o in (d.get("Versions") or []) + (d.get("DeleteMarkers") or [])]
print(json.dumps({"Objects": objs, "Quiet": True}) if objs else "")')
    [ -z "$batch" ] && break
    aws s3api delete-objects --region "$REGION" --bucket "$BUCKET" --delete "$batch" >/dev/null
    n=$((n + $(python3 -c 'import json,sys; print(len(json.loads(sys.argv[1])["Objects"]))' "$batch")))
  done
  echo "bucket $BUCKET: deleted $n object versions"
fi

for r in $REPOS; do
  ids=$(aws ecr list-images --region "$REGION" --repository-name "$r" --query 'imageIds' --output json)
  total=0
  # batch-delete-image takes at most 100 image ids per call
  for chunk in $(python3 -c '
import json, sys
ids = json.loads(sys.argv[1])
for i in range(0, len(ids), 100):
    print(json.dumps(ids[i:i + 100], separators=(",", ":")))' "$ids"); do
    aws ecr batch-delete-image --region "$REGION" --repository-name "$r" --image-ids "$chunk" >/dev/null
    total=$((total + $(python3 -c 'import json,sys; print(len(json.loads(sys.argv[1])))' "$chunk")))
  done
  [ "$total" -gt 0 ] && echo "ecr $r: deleted $total images"
done
echo "emptied"
