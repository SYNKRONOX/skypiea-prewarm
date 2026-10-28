#!/bin/sh
# Pre-commit hook: block commits that would track a file over 50 MB.
# Install with `make install-hooks`.
PY=python
[ -x .venv/Scripts/python.exe ] && PY=.venv/Scripts/python.exe
[ -x .venv/bin/python ] && PY=.venv/bin/python
exec "$PY" tools/check_sizes.py
