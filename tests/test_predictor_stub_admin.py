"""Issue #27: the stub predictor's /admin/policy needs the admin token, validates params at set
time (bad -> 400, never stored), /decide fails open to "nothing warmed" (200, never 500) and the
per-session learning state is bounded."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from gateway import predictor_stub
from gateway.contract import parse_response
from gateway.predictor_stub import StubPredictor, create_app

TOK = "t" * 48
H = {"X-Admin-Token": TOK}
REQ = {"session_id": "s", "now_ms": 10.0,
       "history": [{"tool_class": "read_file", "t_start_ms": 0, "t_end_ms": 5, "error": False,
                    "batch_id": 0}]}


def client(token=TOK):
    return TestClient(create_app(StubPredictor("keep_alive", {"ttl_s": 30}), admin_token=token))


def test_post_policy_needs_the_admin_token():
    c = client()
    assert c.post("/admin/policy", json={"name": "scale_to_zero"}).status_code == 401
    assert c.post("/admin/policy", json={"name": "scale_to_zero"},
                  headers={"X-Admin-Token": "wrong"}).status_code == 401
    assert c.get("/admin/policy").json()["policy"] == "keep_alive"     # unchanged, GET is open
    r = c.post("/admin/policy", json={"name": "scale_to_zero"}, headers=H)
    assert r.status_code == 200 and r.json()["policy"] == "scale_to_zero"


def test_no_token_configured_disables_mutation():
    c = client(token=None)
    assert c.post("/admin/policy", json={"name": "scale_to_zero"}, headers=H).status_code == 403


@pytest.mark.parametrize("body", [
    {"name": "keep_alive", "params": {"ttl_s": "abc"}},
    {"name": "keep_alive", "params": {"ttl_s": -1}},
    {"name": "keep_alive", "params": {"ttl_s": 10**12}},
    {"name": "always_warm", "params": {"ttl_s": None}},
    {"name": "predictive", "params": {"tau": "x"}},
    b'{"name": "predictive", "params": {"tau": NaN}}',
    {"name": "markov1", "params": {"tau": 2}},
    {"name": "markov1", "params": {"bogus": 1}},
    {"name": "keep_alive", "params": [1, 2]},
    {"name": "nope"},
    {"params": {}},
    [1],
])
def test_bad_params_are_rejected_and_never_stored(body):
    c = client()
    r = (c.post("/admin/policy", content=body, headers=H) if isinstance(body, bytes)
         else c.post("/admin/policy", json=body, headers=H))
    assert r.status_code == 400
    assert c.get("/admin/policy").json() == {"policy": "keep_alive", "params": {"ttl_s": 30}}
    d = c.post("/decide", json=REQ)
    assert d.status_code == 200 and d.json()["decisions"]


def test_non_json_body_is_400():
    assert client().post("/admin/policy", content=b"{", headers=H).status_code == 400


def test_decide_fails_open_instead_of_500(monkeypatch):
    def boom(self, req, t0):
        raise ZeroDivisionError("x")
    monkeypatch.setitem(predictor_stub.POLICIES, "keep_alive", boom)
    r = client().post("/decide", json=REQ)
    assert r.status_code == 200
    resp = parse_response(r.json())
    assert resp.decisions == [] and resp.ranked == []


def test_seen_is_bounded(monkeypatch):
    monkeypatch.setattr(predictor_stub, "MAX_SESSIONS", 50)
    s = StubPredictor("markov1", {})
    from gateway.contract import DecideRequest
    for i in range(500):
        s.decide(DecideRequest.from_json({**REQ, "session_id": f"s{i}"}))
    assert len(s.seen) == 50
    assert "s499" in s.seen and "s0" not in s.seen
