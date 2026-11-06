#!/usr/bin/env bash
# `make fetch-models`: download the real predictor bundles from the GitHub Release asset of
# tag v1.2 of this repository, verify them against the release's SHA256SUMS file and the
# per-file checksums in configs/live/models_files.sha256, and unpack them into models/.
#   default URL: https://github.com/<owner>/<repo>/releases/download/v1.2/<asset>
#   (owner/repo from the git remote "origin"); override with MODELS_BASE_URL.
# http:// is accepted for 127.0.0.1/localhost only (tests); everything else must be https.
set -euo pipefail
cd "$(dirname "$0")/.."
TAG="${MODELS_TAG:-v1.2}"
ASSET="${MODELS_ASSET:-skypiea-models-af000231.tar.gz}"
if [ -z "${MODELS_BASE_URL:-}" ]; then
  origin=$(git remote get-url origin 2>/dev/null || true)
  slug=$(printf '%s' "$origin" | sed -E 's#^(git@github.com:|https://github.com/)##; s#\.git$##')
  [ -n "$slug" ] || { echo "no git remote origin: set MODELS_BASE_URL" >&2; exit 1; }
  MODELS_BASE_URL="https://github.com/$slug/releases/download/$TAG"
fi
case "$MODELS_BASE_URL" in
  https://*) proto='=https' ;;
  http://127.0.0.1*|http://localhost*) proto='=http' ;;
  *) echo "refusing $MODELS_BASE_URL: https only (http for localhost)" >&2; exit 1 ;;
esac
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
get() { curl -fsSL --proto "$proto" --max-filesize 50000000 -o "$tmp/$1" "$MODELS_BASE_URL/$1"; }
if ! get SHA256SUMS; then
  echo "The model archive is not published yet: $MODELS_BASE_URL/SHA256SUMS was not found." >&2
  echo "Maintainers: build it with demo/build_models_archive.sh and attach dist/$ASSET and" >&2
  echo "dist/SHA256SUMS to the GitHub Release $TAG (docs/PORTABLE_MODELS.md)." >&2
  exit 2
fi
get "$ASSET" || { echo "SHA256SUMS found but $ASSET is missing from release $TAG" >&2; exit 2; }
(cd "$tmp" && grep " $ASSET\$" SHA256SUMS | sha256sum -c --quiet -) \
  || { echo "checksum mismatch for $ASSET" >&2; exit 3; }
tar -tzf "$tmp/$ASSET" | grep -Eq '(^/|(^|/)\.\.(/|$))' && { echo "unsafe paths" >&2; exit 3; }
mkdir -p "$tmp/x"
tar -xzf "$tmp/$ASSET" -C "$tmp/x" --no-same-owner --no-same-permissions
(cd "$tmp/x" && sha256sum -c --quiet "$OLDPWD/configs/live/models_files.sha256") \
  || { echo "per-file checksum mismatch" >&2; exit 3; }
mkdir -p models
for d in "$tmp"/x/lgbm-*; do rm -rf "models/$(basename "$d")"; mv "$d" models/; done
cp "$tmp/x/ATTRIBUTION.md" models/ATTRIBUTION.md 2>/dev/null || true
echo "bundles in models/: $(ls -d models/lgbm-* | xargs -n1 basename | tr '\n' ' ')"
echo "then: make live BUNDLES_DIR=models"
