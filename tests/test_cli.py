"""skypiea CLI against a fake backend (no cluster needed)."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from skypiea_cli import cli as climod

SESSION = {
    "session_id": "abc", "ended": True, "inflight": 0,
    "calls": [
        {"i": 0, "tool_class": "order_lookup", "server": "order_lookup", "batch_id": 0,
         "t_start_ms": 0.0, "t_end_ms": 2100.0, "cold": True, "held_ms": 150.2,
         "added_ms": 1900.0, "predicted": False, "ok": True},
        {"i": 1, "tool_class": "policy_check", "server": "policy_check", "batch_id": 1,
         "t_start_ms": 9000.0, "t_end_ms": 9030.0, "cold": False, "held_ms": 0.0,
         "added_ms": 2.0, "predicted": True, "ok": True},
        {"i": 2, "tool_class": "refund_issue", "server": "refund_issue", "batch_id": 2,
         "t_start_ms": 9500.0, "t_end_ms": 12000.0, "cold": True, "held_ms": 150.0,
         "added_ms": 2300.0, "predicted": False, "ok": True}],
    "decisions": [
        {"after_call": 0, "t_ms": 2100.0, "outcome": "ok", "model_version": "stub-markov1",
         "latency_ms": 0.4, "ranked": [{"tool_class": "policy_check", "p": 0.7},
                                       {"tool_class": "refund_issue", "p": 0.2}],
         "decisions": [{"tool_class": "policy_check", "warm": True, "copies": 1,
                        "start_in_ms": 0, "ttl_s": None, "prob": 0.7}]},
        {"after_call": 1, "t_ms": 9030.0, "outcome": "error:timeout", "detail": ""}],
}


class Fake:
    namespace = "skypiea-tools"

    def __init__(self):
        self.policy = {"policy": "scale_to_zero", "params": {}}

    def deployments(self):
        return [{"name": "order-lookup", "tool": "order_lookup", "tier": "medium",
                 "replicas": 1, "ready": 1, "image": "x"},
                {"name": "shell", "tool": "shell", "tier": "light", "replicas": 0, "ready": 0,
                 "image": "x"},
                {"name": "skypiea-gateway", "tool": "skypiea-gateway", "tier": None,
                 "replicas": 1, "ready": 1, "image": "p"}]

    def controller_state(self):
        return {"order_lookup": {"live": 1, "ready": 1, "copies": 1, "warm_for_s": 12.5,
                                 "inflight": 0}}

    def get_policy(self):
        return self.policy

    def set_policy(self, name, params):
        self.policy = {"policy": name, "params": params}
        return self.policy

    def sessions(self):
        return [{"session_id": "abc", "calls": 3, "ended": True}]

    def session(self, sid):
        return SESSION if sid == "abc" else None


@pytest.fixture
def run(monkeypatch):
    fake = Fake()
    monkeypatch.setattr(climod, "BACKEND_FACTORY", lambda: fake)

    def go(*args):
        return CliRunner().invoke(climod.cli, list(args), catch_exceptions=False)
    go.fake = fake
    return go


def test_status(run):
    r = run("status")
    assert r.exit_code == 0
    assert "policy: scale_to_zero" in r.output and "1 of 2 tools have a ready pod" in r.output
    assert "order_lookup" in r.output and "12.5" in r.output and "skypiea-gateway" in r.output


def test_policy_set_parses_params(run):
    r = run("policy", "set", "markov1", "-p", "tau=0.6", "-p", "note=x")
    assert r.exit_code == 0 and run.fake.policy == {"policy": "markov1",
                                                    "params": {"tau": 0.6, "note": "x"}}
    assert run("policy", "get").output.startswith("markov1")
    assert run("policy", "set", "x", "-p", "bad").exit_code != 0


def test_trace(run):
    r = run("trace", "abc")
    assert "3 calls, 2 cold (ended)" in r.output
    lines = r.output.splitlines()
    assert any("policy_check" in ln and "warm" in ln and "yes" in ln for ln in lines)


def test_explain_says_why(run):
    out = run("explain", "abc").output
    assert "stub-markov1 ranked policy_check 0.70, refund_issue 0.20" in out
    assert "warm policy_check: 1 pod(s) now, default TTL 30 s (p=0.70)" in out
    assert "next call #1 policy_check: HIT" in out
    assert "predictor failed (timeout)" in out and "FR-4" in out
    assert "next call #2 refund_issue: MISS" in out


def test_unknown_session_and_json(run):
    r = CliRunner().invoke(climod.cli, ["trace", "nope"])
    assert r.exit_code != 0 and "purged" in r.output
    data = json.loads(run("--json", "sessions").output)
    assert data[0]["session_id"] == "abc"
