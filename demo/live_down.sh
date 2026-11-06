#!/usr/bin/env bash
# `make live-clean`: stop the console and the port-forwards, uninstall both stacks.
# The kind cluster and the local registry are kept (they are shared with `make demo`).
set -euo pipefail
cd "$(dirname "$0")/.."
msg="no console was running"
if [ -f .live/console.pid ]; then
  kill "$(cat .live/console.pid)" 2>/dev/null && msg="console stopped"
  rm -f .live/console.pid
fi
demo/live_stacks.sh down
echo "$msg. Exports and run logs stay in .live/ (git-ignored)."
