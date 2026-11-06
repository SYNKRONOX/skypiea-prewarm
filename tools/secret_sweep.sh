#!/usr/bin/env bash
# `make secret-sweep`: look for credentials and AWS account ids in the tracked tree and, with
# HISTORY=1, in every commit of every branch. Prints file names and counts only, never the
# matched text. Exit 1 if anything outside the allow-list is found.
# Allow-list (not secrets): the detection regex in gateway/scenario_verify.py and in this
# script, and 099720109477 (Canonical's public AMI owner id, infra/aws/main.tf).
set -euo pipefail
cd "$(dirname "$0")/.."
KEYS='AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}|aws_secret_access_key|-----BEGIN [A-Z ]*PRIVATE KEY|ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9]{32,}|xox[bp]-[A-Za-z0-9-]{10,}|hf_[A-Za-z0-9]{30,}'
ACCT='arn:aws:[a-z0-9-]*:[a-z0-9-]*:[0-9]{12}|[0-9]{12}\.dkr\.ecr|(^|[^0-9a-f.])[0-9]{12}([^0-9a-f]|$)'
ALLOW_FILES='^(gateway/scenario_verify\.py|tools/secret_sweep\.sh)$'
ALLOW_IDS='099720109477'
SKIP=(':!*.csv' ':!*.csv.gz' ':!*.json' ':!*.jsonl' ':!*.log' ':!*.lock' ':!*.sha256' ':!*.hcl' ':!*.png')
bad=0
k=$(git grep -IlE "$KEYS" | grep -Ev "$ALLOW_FILES" || true)
if [ -n "$k" ]; then echo "credential-like text in:"; printf '  %s\n' $k; bad=1; else echo "tree: no credential-like text"; fi
a=$(git grep -IohE "$ACCT" -- "${SKIP[@]}" | grep -oE '[0-9]{12}' | { grep -vxE "$ALLOW_IDS" || true; } | sort -u | wc -l)
if [ "$a" -gt 0 ]; then
  echo "tree: $a distinct 12-digit number(s) that may be AWS account ids, in:"
  git grep -IlE "$ACCT" -- "${SKIP[@]}" | while read -r f; do
    git grep -IohE "$ACCT" -- "$f" | grep -oE '[0-9]{12}' | grep -qvxE "$ALLOW_IDS" && echo "  $f"
  done
  bad=1
else echo "tree: no AWS account id"; fi
e=$(git grep -IlE '[0-9]{12}\.dkr\.ecr|arn:aws:[a-z0-9-]*:[a-z0-9-]*:[0-9]{12}' || true)   # all files, data too
if [ -n "$e" ]; then echo "unmasked account id in an ECR host or ARN in:"; printf '  %s\n' $e; bad=1; else echo "tree: no unmasked ECR host or ARN (data files included)"; fi
tracked=$(git ls-files | grep -E '(^|/)(terraform\.tfstate[^/]*|[^/]*\.tfvars|[^/]*\.pem|[^/]*\.key|kubeconfig[^/]*|\.env)$' || true)
if [ -n "$tracked" ]; then echo "secret-like files tracked:"; printf '  %s\n' $tracked; bad=1; else echo "tree: no secret-like file names tracked"; fi
if [ "${HISTORY:-0}" = 1 ]; then
  set +o pipefail   # grep finding nothing is the good case here
  n=$(git log --all -p --no-color | grep -E '^\+' | grep -E "$KEYS" | grep -cvE 'KEY_RE = re.compile|^\+KEYS=' || true)
  ids=$(git log --all -p --no-color | grep -E '^\+' | grep -oE 'arn:aws:[a-z0-9-]*:[a-z0-9-]*:[0-9]{12}|[0-9]{12}\.dkr\.ecr' | { grep -oE "[0-9]{12}" || true; } | sort -u | wc -l)
  echo "history ($(git rev-list --all | wc -l) commits): $n credential-like added line(s), $ids account id(s) in ARNs/ECR hosts"
  [ "$n" = 0 ] && [ "$ids" = 0 ] || bad=1
fi
[ $bad = 0 ] && echo "secret sweep: clean" || { echo "secret sweep: FOUND something, see above" >&2; exit 1; }
