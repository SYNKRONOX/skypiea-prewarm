#!/usr/bin/env bash
# `make fetch-models MODELS_URL=<url> MODELS_SHA256=<sha256>`: download the three real
# predictor bundles (one .tar.gz), verify the archive's sha256 AND every file's sha256
# (configs/live/models_files.sha256), and unpack them into models/ for `make live`.
# There is NO default URL: nothing is published until the maintainers decide where
# (docs/PORTABLE_MODELS.md). Read-only otherwise; never runs code from the archive.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${MODELS_URL:?set MODELS_URL (no default: see docs/PORTABLE_MODELS.md)}"
: "${MODELS_SHA256:?set MODELS_SHA256 (the archive's sha256)}"
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
curl -fL --proto '=https' --max-filesize 50000000 -o "$tmp/models.tar.gz" "$MODELS_URL"
echo "$MODELS_SHA256  $tmp/models.tar.gz" | sha256sum -c -
mkdir -p "$tmp/x"
# plain files only, no absolute paths or ..
tar -tzf "$tmp/models.tar.gz" | grep -Eq '(^/|(^|/)\.\.(/|$))' && { echo "unsafe paths" >&2; exit 1; }
tar -xzf "$tmp/models.tar.gz" -C "$tmp/x" --no-same-owner --no-same-permissions
(cd "$tmp/x" && sha256sum -c --quiet "$OLDPWD/configs/live/models_files.sha256")
mkdir -p models
for d in "$tmp"/x/lgbm-*; do rm -rf "models/$(basename "$d")"; mv "$d" models/; done
echo "bundles in models/: $(ls -d models/lgbm-* | xargs -n1 basename | tr '\n' ' ')"
