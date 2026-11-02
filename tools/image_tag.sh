#!/usr/bin/env bash
# Image tag for the current checkout: git short sha, plus -dirty if tools/ or the E0 probe have local changes.
set -euo pipefail
cd "$(dirname "$0")/.."
sha=$(git rev-parse --short HEAD)
if [ -n "$(git status --porcelain -- tools/ experiments/e0_probe/)" ]; then sha="${sha}-dirty"; fi
echo "$sha"
