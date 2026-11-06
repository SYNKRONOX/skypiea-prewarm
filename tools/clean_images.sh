#!/usr/bin/env bash
# `make clean-images`: remove the Docker image tags this repo builds (skypiea/<name>:<tag>) and
# their registry copies (localhost:<port>/skypiea/<name>:<tag>). Other images are not touched.
# Images inside the kind registry and nodes go away with `make demo-clean` / `make clean-cluster`.
set -euo pipefail
tags=$(docker images --format '{{.Repository}}:{{.Tag}}' | grep -E '^(skypiea|localhost:[0-9]+/skypiea)/' || true)
if [ -z "$tags" ]; then echo "no skypiea images"; exit 0; fi
n=$(printf '%s\n' "$tags" | wc -l)
printf '%s\n' "$tags" | xargs docker rmi >/dev/null
echo "removed $n skypiea image tags (docker image prune removes dangling layers)"
