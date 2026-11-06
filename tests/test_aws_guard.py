"""`make aws-*` must refuse safely: no AWS or Terraform call without credentials, and nothing
created without AWS_CONFIRM=yes.

The tests run make with a PATH of basic utilities plus FAKE aws/terraform binaries that only
record that they were called, and HOME pointing at an empty folder (no ~/.aws). The real AWS
CLI and Terraform on the machine are never reachable from these tests.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BASIC = ["bash", "sh", "make", "env", "grep", "sed", "cat", "git", "head", "cut", "date", "awk"]
CREATE = ["aws-apply", "aws-start", "push-images-aws", "push-images-aws-docker", "e0-aws-node"]
PLAIN = ["aws-plan", "aws-stop", "aws-destroy", "aws-tunnel", "aws-kubeconfig", "deploy-aws",
         "e0-aws", "e0-aws-status", "e0-aws-fetch", "aws-gateway", "aws-shell"]


def _env(tmp_path: Path, with_bins: bool = True, creds: bool = False) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for b in BASIC:
        real = shutil.which(b)
        if real and not (bin_dir / b).exists():
            (bin_dir / b).symlink_to(real)
    marker = tmp_path / "called"
    if with_bins:
        for name in ("aws", "terraform"):
            p = bin_dir / name
            p.write_text(f'#!/bin/sh\necho "{name} $*" >> {marker}\nexit 0\n')
            p.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {"PATH": str(bin_dir), "HOME": str(home)}
    if creds:
        env["AWS_PROFILE"] = "fake-profile-for-tests"
    return env


def _make(target: str, env: dict[str, str], *extra: str):
    return subprocess.run(["make", "--no-print-directory", target, *extra], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=60)


@pytest.mark.parametrize("target", CREATE)
def test_create_targets_need_confirmation(tmp_path, target):
    r = _make(target, _env(tmp_path, creds=True))
    assert r.returncode != 0
    assert "refused" in r.stderr and "AWS_CONFIRM=yes" in r.stderr
    assert not (tmp_path / "called").exists(), "aws/terraform was called"
    assert "Traceback" not in r.stdout + r.stderr


@pytest.mark.parametrize("target", PLAIN + CREATE)
def test_no_credentials_refuses_before_any_call(tmp_path, target):
    r = _make(target, _env(tmp_path), "AWS_CONFIRM=yes")
    assert r.returncode != 0
    assert "no AWS credentials are configured" in r.stderr
    assert not (tmp_path / "called").exists()


def test_missing_cli_refuses(tmp_path):
    r = _make("aws-plan", _env(tmp_path, with_bins=False, creds=True))
    assert r.returncode != 0 and "aws CLI v2 is not installed" in r.stderr


@pytest.mark.parametrize("target", ["aws-stop", "aws-destroy", "deploy-aws"])
def test_needs_local_state(tmp_path, target):
    if (ROOT / "infra" / "aws" / "terraform.tfstate").exists():
        pytest.skip("this checkout holds real Terraform state; not testing state-dependent targets")
    r = _make(target, _env(tmp_path, creds=True))
    assert r.returncode != 0 and "no Terraform state" in r.stderr
    assert not (tmp_path / "called").exists()


def test_every_aws_target_is_guarded():
    text = (ROOT / "mk" / "infra.mk").read_text().splitlines()
    for i, line in enumerate(text):
        name = line.split(":")[0]
        if line and not line[0].isspace() and ":" in line and not line.startswith(("#", ".")) \
                and ("aws" in name) and "=" not in line.split(":")[0] and "?=" not in line \
                and not line.startswith(("AWS", "aws-reminder")):
            assert "AWS_GUARD" in text[i + 1], f"{name} has no guard"
