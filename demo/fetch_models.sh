#!/usr/bin/env bash
# `make fetch-models`: download the real predictor bundles from the GitHub Release asset of
# tag v1.2, verify them against the release's SHA256SUMS file and the per-file checksums in
# configs/live/models_files.sha256, and unpack them into models/.
# Where the release is looked up, first match wins:
#   1 MODELS_BASE_URL                       full base URL (tests, mirrors)
#   2 MODELS_REPO=<owner>/<repo>            configured slug; default SYNKRONOX/skypiea-prewarm
#                                           https://github.com/<slug>/releases/download/<tag>/
#   3 MODELS_REPO="" (empty)                the slug of git remote "origin", only if it is GitHub
# FETCH_DRY_RUN=1 prints the URL and exits. http:// is accepted for 127.0.0.1/localhost only.
set -euo pipefail
cd "$(dirname "$0")/.."
TAG="${MODELS_TAG:-v1.2}"
ASSET="${MODELS_ASSET:-skypiea-models-af000231.tar.gz}"
DEFAULT_REPO=SYNKRONOX/skypiea-prewarm
if [ -z "${MODELS_BASE_URL:-}" ]; then
  slug="${MODELS_REPO-$DEFAULT_REPO}"
  if [ -z "$slug" ]; then
    origin=$(git remote get-url origin 2>/dev/null || true)
    case "$origin" in
      git@github.com:*|https://github.com/*|ssh://git@github.com/*)
        slug=$(printf '%s' "$origin" | sed -E 's#^(git@github.com:|https://github.com/|ssh://git@github.com/)##; s#\.git$##; s#/$##') ;;
      *) echo "MODELS_REPO is empty and git remote origin is not a GitHub URL (${origin:-none})." >&2
         echo "Set MODELS_REPO=<owner>/<repo> or MODELS_BASE_URL=<https URL>." >&2; exit 1 ;;
    esac
  fi
  printf '%s' "$slug" | grep -Eq '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$' \
    || { echo "MODELS_REPO must look like <owner>/<repo>, got: $slug" >&2; exit 1; }
  MODELS_BASE_URL="https://github.com/$slug/releases/download/$TAG"
fi
if [ "${FETCH_DRY_RUN:-0}" = 1 ]; then echo "$MODELS_BASE_URL/$ASSET"; exit 0; fi
case "$MODELS_BASE_URL" in
  https://*) proto='=https' ;;
  http://127.0.0.1*|http://localhost*) proto='=http' ;;
  *) echo "refusing $MODELS_BASE_URL: https only (http for localhost)" >&2; exit 1 ;;
esac
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
get() { curl -fsSL --proto "$proto" --max-filesize 50000000 -o "$tmp/$1" "$MODELS_BASE_URL/$1"; }
if ! get SHA256SUMS; then
  echo "The model archive is not available: $MODELS_BASE_URL/SHA256SUMS was not found" >&2
  echo "(release $TAG not published, or no network). Nothing was changed." >&2
  echo "Without it: Tier 1 (make demo) works; make live needs the bundles (README, Tier 2)." >&2
  echo "Maintainers: demo/build_models_archive.sh, then attach dist/$ASSET and dist/SHA256SUMS" >&2
  echo "to the GitHub Release $TAG (docs/PORTABLE_MODELS.md)." >&2
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
