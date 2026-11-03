#!/usr/bin/env bash
# Run ONE command as root on the Skypiea VM through SSM Session Manager (no SSH, no inbound
# port) and print only its output; exits with the command's exit code.
#   infra/aws/scripts/node-run.sh 'k3s crictl images -o json'
# Needs: aws CLI v2 + session-manager-plugin on the laptop, ssm:StartSession on the instance.
set -euo pipefail
cd "$(dirname "$0")/.."
[ $# -eq 1 ] || { echo "usage: $0 '<command>'" >&2; exit 2; }
ID="${SKYPIEA_INSTANCE_ID:-$(terraform output -raw instance_id)}"
REGION="${AWS_REGION:-ap-south-1}"
TAG="SKYPIEA$RANDOM$RANDOM"
# The session prints banners around the output and always exits 0, so the command's output is
# framed with markers and its exit code is passed back on the end marker.
# AWS-StartNonInteractiveCommand does not run the command through a shell (it splits it into
# argv, honouring quotes), so the payload is an explicit bash -c '...'. The user's command
# travels base64-encoded inside it (no quotes, no spaces), so no quoting reaches the VM.
B64=$(printf '%s' "$1" | base64 -w0)
INNER="bash -c 'echo ${TAG}_BEGIN; echo $B64 | base64 -d | sudo -n bash 2>&1; echo ${TAG}_END \$?'"
PARAMS=$(python3 -c 'import json,sys; print(json.dumps({"command": [sys.argv[1]]}))' "$INNER")
# session-manager-plugin ends the session with "Cannot perform start session: EOF" as soon as
# its stdin hits EOF, so stdin is held open by a sleep that is killed afterwards.
exec 3< <(sleep 3600)
HOLD=$!
OUT=$(aws ssm start-session --region "$REGION" --target "$ID" \
        --document-name AWS-StartNonInteractiveCommand --parameters "$PARAMS" 2>&1 <&3) || {
  kill "$HOLD" 2>/dev/null; echo "ssm start-session failed: $OUT" >&2; exit 125; }
kill "$HOLD" 2>/dev/null || true
exec 3<&-
printf '%s\n' "$OUT" | tr -d '\r' | python3 -c '
import sys
tag = sys.argv[1]
lines = sys.stdin.read().split("\n")
try:
    b = lines.index(tag + "_BEGIN")
    e = next(i for i in range(len(lines) - 1, b, -1) if lines[i].startswith(tag + "_END "))
except (ValueError, StopIteration):
    sys.stderr.write("no command output framed by markers; raw session output:\n" + "\n".join(lines) + "\n")
    sys.exit(125)
sys.stdout.write("\n".join(lines[b + 1:e]) + ("\n" if e > b + 1 else ""))
sys.exit(int(lines[e].split()[1]))' "$TAG"
