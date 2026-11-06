#!/usr/bin/env bash
# Build every tool image in tools/tool_map.yaml, ONE AT A TIME (the laptop is shared with
# model training). Tags: skypiea/<tool>:<git-short-sha> (+ "-dirty" if the tree has
# uncommitted changes, so an image never claims a commit it was not built from).
#
#   tools/build_images.sh                 # all tools
#   TOOLS="user_lookup run_tests" tools/build_images.sh
set -euo pipefail
cd "$(dirname "$0")/.."

TAG="${TAG:-$(tools/image_tag.sh)}"
PY="${PY:-$( [ -x .venv/bin/python ] && echo .venv/bin/python || echo python3)}"
TOOLS="${TOOLS:-$("$PY" -c 'import yaml;print(" ".join(yaml.safe_load(open("tools/tool_map.yaml"))["servers"]))')}"

for t in $TOOLS; do
  echo "==> building skypiea/${t}:${TAG}"
  start=$(date +%s)
  DOCKER_BUILDKIT=1 docker build --quiet --file "tools/${t}/Dockerfile" \
    --tag "skypiea/${t}:${TAG}" --label "org.opencontainers.image.revision=${TAG}" tools/ >/dev/null
  size=$(docker image inspect "skypiea/${t}:${TAG}" --format '{{.Size}}')
  printf '    %-18s %6.0f MB  (%ss)\n' "$t" "$(awk -v s="$size" 'BEGIN{print s/1000000}')" "$(( $(date +%s) - start ))"
done

# The E0 in-cluster probe (not a tool; same tag).
if [ "${WITH_PROBE:-1}" = "1" ]; then
  echo "==> building skypiea/e0-probe:${TAG}"
  DOCKER_BUILDKIT=1 docker build --quiet --file experiments/e0_probe/Dockerfile \
    --tag "skypiea/e0-probe:${TAG}" experiments/e0_probe/ >/dev/null
fi

# The platform image: gateway, controller, predictor placeholder (context = repo root).
if [ "${WITH_PLATFORM:-1}" = "1" ]; then
  echo "==> building skypiea/platform:${TAG}"
  DOCKER_BUILDKIT=1 docker build --quiet --file deploy/platform/Dockerfile \
    --tag "skypiea/platform:${TAG}" . >/dev/null
fi
