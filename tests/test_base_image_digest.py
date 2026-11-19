"""Issue #3: every image builds on the refreshed python:3.12-slim-bookworm digest (apt 2.6.2,
CVE-2026-109518 fixed), never the old one."""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OLD = "e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337"
NEW = "935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df"


def test_no_tracked_file_uses_the_old_base_digest():
    out = subprocess.run(["git", "grep", "-l", OLD, "--", ".", ":!tests/"], cwd=ROOT,
                         capture_output=True, text=True).stdout.split()
    assert out == []


def test_dockerfiles_pin_the_new_digest():
    dfs = [p for p in ROOT.glob("tools/*/Dockerfile")]
    assert dfs and all(NEW in p.read_text() for p in dfs
                       if "python:3.12-slim-bookworm@" in p.read_text())
