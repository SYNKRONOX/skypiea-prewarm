"""Issue #13 (static, no AWS call): the operator can manage the node role only under the
skypiea-node-boundary permissions boundary, which it can neither remove nor edit."""

from __future__ import annotations

import json
import re
from pathlib import Path

AWS = Path(__file__).resolve().parent.parent / "infra" / "aws"
POL = json.loads((AWS / "operator-policy.json").read_text())
BOUNDARY_ARN = "arn:aws:iam::*:policy/skypiea-node-boundary"


def actions(st):
    a = st["Action"]
    return [a] if isinstance(a, str) else a


def allows(action):
    return [s for s in POL["Statement"] if s["Effect"] == "Allow" and action in actions(s)]


def test_role_writes_need_the_boundary():
    for a in ("iam:CreateRole", "iam:PutRolePolicy"):
        for st in allows(a):
            assert st["Condition"]["ArnLike"]["iam:PermissionsBoundary"] == BOUNDARY_ARN, a


def test_boundary_cannot_be_removed_or_edited():
    deny = [s for s in POL["Statement"] if s["Effect"] == "Deny"]
    denied = {a for s in deny for a in actions(s)}
    assert {"iam:DeleteRolePermissionsBoundary", "iam:CreatePolicyVersion",
            "iam:DeletePolicy", "iam:SetDefaultPolicyVersion"} <= denied
    assert not allows("iam:AttachRolePolicy")      # no managed policies on the node role


def test_boundary_policy_is_narrow_and_terraform_uses_it():
    b = json.loads((AWS / "node-boundary-policy.json").read_text())
    acts = [a for s in b["Statement"] for a in actions(s)]
    assert "*" not in acts and not any(a.startswith("iam:") for a in acts)
    assert not re.search(r"\d{12}", json.dumps(b))
    tf = (AWS / "main.tf").read_text()
    assert re.search(r"permissions_boundary\s*=.*skypiea-node-boundary", tf)


def test_policy_documents_are_valid_json_with_unique_sids():
    for f in ("operator-policy.json", "node-boundary-policy.json"):
        d = json.loads((AWS / f).read_text())
        sids = [s["Sid"] for s in d["Statement"]]
        assert len(sids) == len(set(sids)) and d["Version"] == "2012-10-17"
