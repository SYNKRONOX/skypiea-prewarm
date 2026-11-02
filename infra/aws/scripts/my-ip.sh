#!/usr/bin/env bash
# Print this machine's public IPv4 (as AWS sees it).
set -euo pipefail
curl -fsS https://checkip.amazonaws.com | tr -d '[:space:]'; echo
