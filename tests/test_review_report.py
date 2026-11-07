"""`make review` / `make report` scripts (tools/review.sh, tools/report.sh): offline checks only.

Tier 1 of `make review` needs Docker and is not run here."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("script", ["tools/review.sh", "tools/report.sh", "tools/test_docker.sh"])
def test_scripts_parse_and_are_executable(script: str) -> None:
    p = ROOT / script
    assert os.access(p, os.X_OK)
    subprocess.run(["bash", "-n", str(p)], check=True)


def test_review_rejects_an_unknown_mode() -> None:
    r = subprocess.run(["tools/review.sh"], cwd=ROOT, env={**os.environ, "MODE": "bogus"},
                       capture_output=True, text=True)
    assert r.returncode == 2 and "MODE=test|all" in r.stderr


def test_review_never_uses_the_default_cluster_or_ports() -> None:
    s = (ROOT / "tools/review.sh").read_text()
    assert 'CLUSTER="${REVIEW_CLUSTER:-skypiea-review}"' in s
    assert 'REG_NAME="${REVIEW_REG_NAME:-kind-registry-review}"' in s
    assert 'PF_BASE="${REVIEW_PF_BASE:-28080}"' in s
    # deletes only what it created: clean-up is reachable only after created=1
    assert s.index("created=1") < s.index("demo-clean")


@pytest.mark.skipif(shutil.which("make") is None, reason="needs make")
def test_report_has_no_home_paths_or_secrets(tmp_path: Path) -> None:
    home = str(Path.home())
    env = {**os.environ, "SKYPIEA_TEST_TOKEN": "tok-should-not-appear"}
    r = subprocess.run(["tools/report.sh"], cwd=ROOT, env=env, capture_output=True, text=True,
                       timeout=120)
    assert r.returncode == 0
    out = r.stdout
    assert "### Skypiea environment report" in out and "#### make doctor" in out
    if home not in ("/", ""):
        assert home not in out
    assert str(ROOT) not in out
    assert "tok-should-not-appear" not in out
