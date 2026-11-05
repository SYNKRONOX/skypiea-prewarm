#!/usr/bin/env bash
# E0 on the k3s node, unattended (systemd unit skypiea-e0, see e0-node.sh). Runs from the
# exported bundle in /opt/skypiea-e0/<run id>/ and needs no laptop connection:
#   1. sequential n=10 (cached + uncached, all tools), then parallel-3 n=10, with --resume, so a
#      unit restart (Restart=on-failure) continues from the partial CSVs;
#   2. uploads results/ + job.log + run_params.json to s3://<bucket>/e0/<run id>/ and verifies
#      every object (size + SHA-256 checksum from S3 against the local file);
#   3. only if every upload verified: shutdown -h now (instance-initiated shutdown = STOP).
#      If an upload fails it exits 3 (no restart, no shutdown) and says so in job_state.
set -euo pipefail
cd "$(dirname "$0")/../../.."          # bundle root
# shellcheck disable=SC1091
set -a; . ./job.env; set +a           # RUN_ID BUCKET REGION GIT_COMMIT N
AWS=/snap/bin/aws
RES=results
mkdir -p "$RES"
state() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "$RES/job_state"; }
COMMON=(--target aws --on-node --n "$N" --modes cached,uncached --tools all --resume
        --no-report --git-commit "$GIT_COMMIT" --image-sizes image_sizes.json)

if [ ! -f "$RES/run_params.json" ]; then
  python3 - "$RES/run_params.json" "${COMMON[@]}" <<'PY'
import json, os, platform, subprocess, sys, time
def out(*c):
    try:
        return subprocess.run(c, capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception as e:  # recorded, not fatal
        return f"unavailable: {e}"
tok = out("curl", "-s", "-m", "5", "-X", "PUT", "http://169.254.169.254/latest/api/token",
          "-H", "X-aws-ec2-metadata-token-ttl-seconds: 60")
md = lambda p: out("curl", "-s", "-m", "5", "-H", f"X-aws-ec2-metadata-token: {tok}",
                   f"http://169.254.169.254/latest/meta-data/{p}")
k3s = out("k3s", "--version")
json.dump({"run_id": os.environ["RUN_ID"], "git_commit": os.environ["GIT_COMMIT"],
           "n": int(os.environ["N"]),
           "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "instance_type": md("instance-type"),
           "availability_zone": md("placement/availability-zone"),
           "kernel": platform.release(), "python": platform.python_version(),
           "k3s": k3s.splitlines()[0] if k3s else None, "nproc": os.cpu_count(),
           "stages": ["parallel=1", "parallel=3"], "harness_args": sys.argv[2:]},
          open(sys.argv[1], "w"), indent=1)
PY
fi

state "E0 sequential: start (attempt of the unit: systemd restarts resume)"
python3 -m experiments.e0_cold_start "${COMMON[@]}"
state "E0 sequential: done"
state "E0 parallel-3: start"
python3 -m experiments.e0_cold_start "${COMMON[@]}" --parallel 3
state "E0 parallel-3: done"

# ---- upload + verify -------------------------------------------------------------------
cp -f job.log "$RES/job.log" 2>/dev/null || true
cp -f "$RES/job_state" "$RES/job_state.txt"
PREFIX="e0/$RUN_ID"
python3 - "$RES" > "$RES/manifest.json" <<'PY'
import hashlib, json, pathlib, sys
d = pathlib.Path(sys.argv[1])
print(json.dumps({p.name: {"bytes": p.stat().st_size,
                           "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                  for p in sorted(d.iterdir())
                  if p.is_file() and p.name not in ("manifest.json", "job_state")}, indent=1))
PY
fail=0
for f in "$RES"/*; do
  name=$(basename "$f")
  [ "$name" = job_state ] && continue
  want_b64=$(openssl dgst -sha256 -binary "$f" | base64 -w0)
  want_size=$(stat -c %s "$f")
  ok=0
  for try in 1 2 3; do
    if $AWS s3api put-object --region "$REGION" --bucket "$BUCKET" --key "$PREFIX/$name" \
         --body "$f" --checksum-algorithm SHA256 --output text --query ChecksumSHA256 \
         >/dev/null 2>"$RES/.upload_err"; then
      got=$($AWS s3api head-object --region "$REGION" --bucket "$BUCKET" --key "$PREFIX/$name" \
            --checksum-mode ENABLED --query '[ContentLength,ChecksumSHA256]' --output text)
      if [ "$got" = "$want_size"$'\t'"$want_b64" ]; then ok=1; break; fi
      echo "verify mismatch for $name: s3=[$got] local=[$want_size $want_b64]" >&2
    else
      echo "put-object $name failed (try $try): $(tail -c 300 "$RES/.upload_err")" >&2
    fi
    sleep 10
  done
  if [ $ok = 1 ]; then echo "uploaded+verified $name ($want_size B)"; else fail=1; fi
done
if [ $fail != 0 ]; then
  state "UPLOAD FAILED: not shutting down; results stay in $(pwd)/$RES"
  exit 3
fi
state "UPLOAD VERIFIED to s3://$BUCKET/$PREFIX/; shutting down (stop)"
$AWS s3api put-object --region "$REGION" --bucket "$BUCKET" --key "$PREFIX/job_state" \
  --body "$RES/job_state" >/dev/null || true
sync
shutdown -h now
