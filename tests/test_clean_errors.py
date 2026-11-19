"""Issue #24: a wrong --context or a missing/bad config gives one clean error line and a
non-zero exit, never a traceback (and no local paths)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def run(*args, env_kube=None, tmp_path=None):
    env = None
    if env_kube is not None:
        import os
        env = {**os.environ, "KUBECONFIG": str(env_kube)}
    return subprocess.run([sys.executable, *args], cwd=ROOT, capture_output=True, text=True,
                          timeout=60, env=env)


@pytest.fixture
def kubeconfig(tmp_path):
    k = tmp_path / "config"
    k.write_text("""apiVersion: v1
kind: Config
clusters: [{name: c, cluster: {server: "https://127.0.0.1:1"}}]
users: [{name: u, user: {token: x}}]
contexts: [{name: real, context: {cluster: c, user: u}}]
current-context: real
""")
    return k


def test_cli_unknown_context_is_one_clean_line(kubeconfig):
    p = run("-m", "skypiea_cli", "--context", "does-not-exist", "status", env_kube=kubeconfig)
    assert p.returncode != 0
    assert "Traceback" not in p.stderr and "Traceback" not in p.stdout
    assert "kube context 'does-not-exist'" in p.stderr
    assert str(kubeconfig.parent) not in p.stderr


def test_replayer_missing_config_is_one_clean_line():
    p = run("-m", "replayer.replay", "--config", "/nonexistent.yaml")
    assert p.returncode == 2 and "Traceback" not in p.stderr
    assert p.stderr.strip() == "replay: config file not found: /nonexistent.yaml"


def test_replayer_bad_config_is_one_clean_line(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("nonsense_key: 1\n")
    p = run("-m", "replayer.replay", "--config", str(bad))
    assert p.returncode == 2 and "Traceback" not in p.stderr
    assert p.stderr.startswith("replay: bad config")
