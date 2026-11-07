#!/usr/bin/env bash
# `make test-docker`: Tier 0 (size check, ruff, check-claims, pytest) inside a python:3.12
# container, for machines without Python 3.12. Needs Docker, and network for the first image
# pull and the pip install. The files git would track (tracked + new, not ignored) are streamed
# into the container; nothing is mounted, so your folder is never written to (no root-owned
# files) and .venv/, models/ and other ignored files stay out. Exit code = the container's.
set -euo pipefail
cd "$(dirname "$0")/.."
IMAGE="${TEST_DOCKER_IMAGE:-python:3.12-bookworm}"
docker info >/dev/null 2>&1 || {
  echo "make test-docker: Docker is not running (make doctor DOCTOR_MODE=demo says how to start it)" >&2
  exit 1; }
git ls-files -z --cached --others --exclude-standard | tar --null -T - -cf - \
| docker run -i --rm "$IMAGE" bash -euo pipefail -c '
  mkdir /w && cd /w && tar -xf -
  git init -q && git add -A && git -c user.name=t -c user.email=t@example.com commit -qm snapshot
  python --version
  pip install -q --root-user-action=ignore -r requirements.lock && pip check
  python tools/check_sizes.py
  ruff check .
  python tools/check_claims.py
  pytest -q
'
