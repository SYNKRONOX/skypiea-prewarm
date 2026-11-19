"""Issue #11 (static, no AWS call): the operator policy tags only on create, only with
project=skypiea, so it cannot put the project tag on resources it did not create."""

from __future__ import annotations

import json
from pathlib import Path

AWS = Path(__file__).resolve().parent.parent / "infra" / "aws"
POL = json.loads((AWS / "operator-policy.json").read_text())
BOUNDARY_ARN = "arn:aws:iam::*:policy/skypiea-node-boundary"


def actions(st):
    a = st["Action"]
    return [a] if isinstance(a, str) else a


def allows(action):
    return [s for s in POL["Statement"] if s["Effect"] == "Allow" and action in actions(s)]


def test_create_tags_only_on_create_with_the_project_tag():
    sts = allows("ec2:CreateTags")
    assert sts
    for st in sts:
        cond = st["Condition"]
        assert set(cond["StringEquals"]["ec2:CreateAction"]) <= {
            "RunInstances", "CreateSecurityGroup", "AuthorizeSecurityGroupEgress"}
        assert cond["StringEquals"]["aws:RequestTag/project"] == "skypiea"
        assert actions(st) == ["ec2:CreateTags"]


def test_run_instances_and_security_groups_must_carry_the_tag():
    run = next(s for s in POL["Statement"] if s["Sid"] == "RunOnlyM7iFlexLarge")
    assert run["Condition"]["StringEquals"]["aws:RequestTag/project"] == "skypiea"
    sg = allows("ec2:CreateSecurityGroup")
    assert all(s["Condition"]["StringEquals"].get("aws:RequestTag/project") == "skypiea"
               for s in sg)
