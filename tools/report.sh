#!/usr/bin/env bash
# `make report`: environment summary to paste into a GitHub issue. Changes nothing.
# Prints OS, tool versions, `make doctor` and the last test summary (from .review/test.log if
# `make review` ran, else it says how to get one). It prints no environment variables, no
# tokens, no kubeconfig content and no paths under your home directory ($HOME is shown as ~).
# Lines that look like credentials are dropped by a last filter.
set -uo pipefail
cd "$(dirname "$0")/.."
ver() {   # ver <label> <command...>: first line of the command's output, or "not found"
  local label=$1; shift
  if command -v "$1" >/dev/null 2>&1; then
    printf '%-10s %s\n' "$label" "$("$@" 2>&1 | head -1)"
  else printf '%-10s not found\n' "$label"; fi
}
{
  echo "### Skypiea environment report"
  echo
  echo '```'
  echo "commit     $(git rev-parse --short HEAD 2>/dev/null || echo '?') ($(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?'))$(git diff --quiet 2>/dev/null || echo ', local changes')"
  echo "os         $(uname -srm)"
  if [ -r /etc/os-release ]; then echo "distro     $(. /etc/os-release; echo "${PRETTY_NAME:-?}")"; fi
  if grep -qi microsoft /proc/version 2>/dev/null; then echo "wsl        yes"; fi
  ver python3.12 python3.12 --version
  ver python3   python3 --version
  if [ -x .venv/bin/python ]; then echo "venv       $(.venv/bin/python --version 2>&1)"; else echo "venv       none (make setup)"; fi
  ver make      make --version
  ver git       git --version
  ver docker    docker version --format 'client {{.Client.Version}}, server {{.Server.Version}}'
  ver kind      kind version
  ver kubectl   kubectl version --client
  ver helm      helm version --short
  ver ollama    ollama --version
  echo "cpus       $(nproc 2>/dev/null || echo '?')"
  echo '```'
  echo
  echo "#### make doctor"
  echo
  echo '```'
  make --no-print-directory doctor 2>&1
  echo '```'
  echo
  echo "#### last test summary"
  echo
  echo '```'
  if [ -f .review/test.log ]; then
    grep -E '[0-9]+ (passed|failed)|error' .review/test.log | tail -3
    echo "(from .review/test.log, $(date -r .review/test.log -u '+%Y-%m-%d %H:%M UTC' 2>/dev/null))"
  else
    echo "no saved test run: run 'make review MODE=test' (or 'make test') and paste its last line"
  fi
  echo '```'
} 2>&1 | sed -e "s#$(pwd)#<repo>#g" -e "s#${HOME}#~#g" \
  | grep -viE '(aws_secret|aws_access_key|secret[_-]?key|api[_-]?key|token|password|passwd|bearer|AKIA[0-9A-Z]{12,})'
exit 0
