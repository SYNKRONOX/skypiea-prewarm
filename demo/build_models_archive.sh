#!/usr/bin/env bash
# Reproducible archive of the three real predictor bundles for a GitHub Release (NOT
# published by this script): dist/skypiea-models-af000231.tar.gz + dist/SHA256SUMS.
#   BUNDLES_DIR=models demo/build_models_archive.sh
set -euo pipefail
cd "$(dirname "$0")/.."
BUNDLES_DIR="${BUNDLES_DIR:-models}"
NAME=skypiea-models-af000231.tar.gz
B="lgbm-coding-af00023 lgbm-support-aabdfd2 lgbm-prd-a7642d0"
mkdir -p dist
st=$(mktemp -d); trap 'rm -rf "$st"' EXIT
for b in $B; do cp -r "$BUNDLES_DIR/$b" "$st/$b"; done
cp docs/MODELS_ATTRIBUTION.md "$st/ATTRIBUTION.md"
(cd "$st" && sha256sum -c --quiet "$OLDPWD/configs/live/models_files.sha256")
(cd "$st" && tar --sort=name --mtime='2026-11-04 00:00:00' --owner=0 --group=0 --numeric-owner \
   -cf - ATTRIBUTION.md $B) | gzip -n -9 > "dist/$NAME"
(cd dist && sha256sum "$NAME" > SHA256SUMS)
cat dist/SHA256SUMS; ls -l "dist/$NAME" | awk '{print $5 " bytes"}'
