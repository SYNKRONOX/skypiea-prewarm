"""tools/doctor.sh: clear FAIL lines with a fix, exit 1, and no Python traceback.

Each test runs a copy of the script in an empty folder (no .venv) with a PATH that holds only
the basic utilities plus fake docker/kind/kubectl/helm, so the result does not depend on the
machine running the tests.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASIC = ["bash", "sh", "uname", "grep", "awk", "df", "cat", "ls", "sed", "env", "make", "git"]


def _fake(bin_dir: Path, name: str, body: str) -> None:
    p = bin_dir / name
    p.write_text("#!/bin/sh\n" + body + "\n")
    p.chmod(0o755)


def _run(tmp_path: Path, mode: str, fakes: dict[str, str], extra_env: dict[str, str] | None = None):
    work = tmp_path / "repo"
    (work / "tools").mkdir(parents=True)
    shutil.copy(ROOT / "tools" / "doctor.sh", work / "tools" / "doctor.sh")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for b in BASIC:
        real = shutil.which(b)
        if real:
            (bin_dir / b).symlink_to(real)
    for name, body in fakes.items():
        _fake(bin_dir, name, body)
    env = {"PATH": str(bin_dir), "HOME": str(tmp_path), "AGENT_BASE_URL": "http://127.0.0.1:9/v1"}
    env.update(extra_env or {})
    return subprocess.run(["bash", str(work / "tools" / "doctor.sh"), mode], cwd=work, env=env,
                          capture_output=True, text=True, timeout=60)


PY_OK = {"python3.12": 'case "$*" in *version*) echo 3.12.9;; esac; exit 0'}
K8S_OK = {
    "kind": 'case "$1" in version) echo "kind v0.33.0 go1 linux/amd64";; get) exit 0;; esac',
    "kubectl": 'echo "Client Version: v1.37.2"',
    "helm": 'echo v4.3.1',
    "curl": "exit 7", "sha256sum": "exit 0", "tar": "exit 0",
}


def test_unknown_mode_is_usage_error(tmp_path):
    r = _run(tmp_path, "bogus", {})
    assert r.returncode == 2 and "usage" in r.stderr


def test_missing_python_fails_with_fix(tmp_path):
    r = _run(tmp_path, "test", {})
    assert r.returncode == 1
    assert "FAIL  Python 3.12 not found" in r.stdout
    assert "fix: " in r.stdout and "python3.12-venv" in r.stdout
    assert "Traceback" not in r.stdout + r.stderr


def test_test_mode_passes_with_python(tmp_path):
    r = _run(tmp_path, "test", PY_OK)
    assert r.returncode == 0, r.stdout
    assert "all required checks passed" in r.stdout
    assert "docker" not in r.stdout.lower()          # test mode does not need Docker


def test_no_docker_fails_with_install_hint(tmp_path):
    r = _run(tmp_path, "demo", {**PY_OK, **K8S_OK})
    assert r.returncode == 1
    assert "FAIL  docker not found" in r.stdout
    assert "docs.docker.com/engine/install" in r.stdout


def test_docker_stopped_fails_with_start_hint(tmp_path):
    r = _run(tmp_path, "demo", {**PY_OK, **K8S_OK, "docker": "exit 1"})
    assert r.returncode == 1
    assert "daemon is not reachable" in r.stdout and "start Docker Desktop" in r.stdout


def test_docker_low_memory_fails(tmp_path):
    docker = ('case "$1" in info) case "$*" in *MemTotal*) echo 2000000000;; esac; exit 0;; '
              'version) echo 29.9.2;; inspect) exit 1;; esac')
    r = _run(tmp_path, "demo", {**PY_OK, **K8S_OK, "docker": docker},
             {"DOCTOR_MIN_DISK_GB": "0"})
    assert r.returncode == 1
    assert "Docker memory 2 GB (need >= 6 GB)" in r.stdout and ".wslconfig" in r.stdout


def test_all_ok_demo(tmp_path):
    docker = ('case "$1" in info) case "$*" in *MemTotal*) echo 16000000000;; esac; exit 0;; '
              'version) echo 29.9.2;; inspect) exit 1;; esac')
    r = _run(tmp_path, "demo", {**PY_OK, **K8S_OK, "docker": docker},
             {"DOCTOR_MIN_DISK_GB": "0", "PF_BASE": "38080", "GRAFANA_PORT": "38090",
              "REG_PORT": "38099"})
    assert "FAIL" not in r.stdout, r.stdout
    assert r.returncode == 0


def test_quiet_mode_prints_only_problems(tmp_path):
    r = _run(tmp_path, "test", PY_OK, {"DOCTOR_QUIET": "1"})
    assert r.returncode == 0 and r.stdout.strip() == ""


def test_makefile_has_doctor_target():
    out = subprocess.run(["make", "-n", "doctor"], cwd=ROOT, capture_output=True, text=True,
                         env={**os.environ})
    assert "tools/doctor.sh" in out.stdout


def test_live_without_bundles_fails_with_fetch_hint(tmp_path):
    docker = ('case "$1" in info) case "$*" in *MemTotal*) echo 16000000000;; esac; exit 0;; '
              'version) echo 29.9.2;; inspect|image) exit 1;; esac')
    r = _run(tmp_path, "live", {**PY_OK, **K8S_OK, "docker": docker},
             {"DOCTOR_MIN_DISK_GB": "0", "PF_BASE": "38080", "GRAFANA_PORT": "38090",
              "REG_PORT": "38099", "CONSOLE_PORT": "38091"})
    assert r.returncode == 1
    assert "predictor bundles are not in models/" in r.stdout and "make fetch-models" in r.stdout
    assert "console needs an LLM" in r.stdout


def test_wrong_python_version_is_named(tmp_path):
    fake = {"python3": 'case "$*" in *platform*) echo 3.11.9; exit 0;; '
                       '*version_info*) exit 1;; esac; exit 0'}
    r = _run(tmp_path, "test", fake, {"PYTHON": "python3"})
    assert r.returncode == 1
    assert "python3 is Python 3.11.9, not 3.12" in r.stdout
