"""Static checks of infra/aws (no AWS calls, no terraform binary needed) and the SSM node-run
wrapper against a fake `aws` CLI."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AWS = ROOT / "infra" / "aws"
# HCL without comments: the checks look at configuration, not at explanations
TF = "\n".join(re.sub(r"#.*", "", (AWS / f).read_text())
               for f in ("main.tf", "variables.tf", "outputs.tf"))

FAKE_AWS = r"""#!/usr/bin/env bash
# fake `aws ssm start-session`: run the command locally (no sudo), wrapped in SSM banners
while [ $# -gt 0 ]; do [ "$1" = --parameters ] && P="$2"; shift; done
CMD=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["command"][0])' "$P" \
      | sed "s/sudo -n bash/bash/")
printf '\r\nStarting session with SessionId: t-1\r\n'
sh -c "$CMD" | sed 's/$/\r/'
printf '\r\n\r\nExiting session with sessionId: t-1.\r\n\r\n'
"""


def test_no_inbound_rules_no_ssh_no_autostop():
    assert "aws_vpc_security_group_ingress_rule" not in TF
    assert not re.search(r"^\s*ingress\s*\{", TF, re.M)
    assert "aws_key_pair" not in TF and "key_name" not in TF
    assert "aws_cloudwatch_metric_alarm" not in TF and "poweroff" not in (
        AWS / "user_data.sh.tftpl").read_text()


def test_only_egress_is_https_and_imds_v2_encrypted_root():
    egress = re.findall(r'resource "aws_vpc_security_group_egress_rule" "(\w+)"', TF)
    assert egress == ["https"]
    assert re.search(r"from_port\s*=\s*443", TF) and not re.search(r"from_port\s*=\s*80\b", TF)
    assert re.search(r'http_tokens\s*=\s*"required"', TF)
    assert re.search(r"encrypted\s*=\s*true", TF)


def test_instance_role_has_no_managed_broad_policies():
    assert "aws_iam_role_policy_attachment" not in TF   # inline, scoped policies only
    assert "AmazonSSMManagedInstanceCore" not in TF
    assert "ecr:PutImage" not in TF                     # the node pulls, never pushes


def test_operator_policy_is_valid_and_scoped():
    pol = json.loads((AWS / "operator-policy.json").read_text())
    run = [s for s in pol["Statement"] if s["Sid"] == "RunOnlyM7iFlexLarge"][0]
    assert run["Condition"]["StringEquals"]["ec2:InstanceType"] == "m7i-flex.large"
    assert not re.search(r"\d{12}", json.dumps(pol))    # never an account id in git


def _node_run(tmp_path: Path, cmd: str) -> subprocess.CompletedProcess:
    fake = tmp_path / "aws"
    fake.write_text(FAKE_AWS)
    fake.chmod(0o755)
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}",
           "SKYPIEA_INSTANCE_ID": "i-test"}
    return subprocess.run([str(AWS / "scripts" / "node-run.sh"), cmd], capture_output=True,
                          text=True, env=env, timeout=30)


def test_node_run_returns_only_output_and_exit_code(tmp_path):
    p = _node_run(tmp_path, "echo \"it's $((1+1))\"; printf 'a b\\n\\nc\\n'; echo err >&2; exit 3")
    assert p.returncode == 3
    assert p.stdout == "it's 2\na b\n\nc\nerr\n"


def test_node_run_success_is_zero(tmp_path):
    p = _node_run(tmp_path, "true")
    assert (p.returncode, p.stdout) == (0, "")


def test_e0_node_job_stops_only_after_verified_upload():
    job = (AWS / "scripts" / "e0-node-job.sh").read_text()
    unit = (AWS / "e0-node.service").read_text()
    assert job.index("--checksum-mode ENABLED") < job.rindex("shutdown -h now")
    assert job.index("exit 3") < job.rindex("shutdown -h now")      # failed upload: no shutdown
    assert "RestartPreventExitStatus=3" in unit and "Restart=on-failure" in unit
    assert "[Install]" not in unit                                  # never started at boot
    assert "--resume" in job and "terminate" not in job
