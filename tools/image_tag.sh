#!/usr/bin/env bash
# Image tag = short sha of the last commit that changed an image input (tools/ or the E0 probe),
# plus -dirty if those paths have uncommitted changes. Commits that only touch results or docs
# therefore keep the tag (and the images) unchanged. E0 also records each image's digest.
set -euo pipefail
cd "$(dirname "$0")/.."
paths=(tools/ experiments/e0_probe/)
sha=$(git log -1 --format=%h -- "${paths[@]}")
if [ -n "$(git status --porcelain -- "${paths[@]}")" ]; then sha="${sha}-dirty"; fi
echo "$sha"
