"""PREDICTOR_DECIDE_OVERRIDES (live console lookahead): unset = unchanged; lookahead only."""

from __future__ import annotations

import pytest

pytest.importorskip("lightgbm")
pytest.importorskip("starlette")

from starlette.testclient import TestClient  # noqa: E402

from services.predictor.app import Settings, make_app  # noqa: E402
from services.predictor.engine import (  # noqa: E402
    BundleError,
    load_bundle,
    read_folder,
    with_decide_overrides,  # noqa: E402
)
from tests.test_service import TOKEN, _req, env  # noqa: E402,F401


def _client(env, monkeypatch, value):  # noqa: F811
    if value is None:
        monkeypatch.delenv("PREDICTOR_DECIDE_OVERRIDES", raising=False)
    else:
        monkeypatch.setenv("PREDICTOR_DECIDE_OVERRIDES", value)
    s = Settings(str(env["a"]), str(env["cold_path"]), TOKEN, str(env["root"]), ())
    return TestClient(make_app(s))


def test_unset_means_bundle_policy_and_no_healthz_field(env, monkeypatch) -> None:  # noqa: F811
    with _client(env, monkeypatch, None) as c:
        h = c.get("/healthz").json()
        assert "decide_overrides" not in h and "lookahead" not in h


def test_lookahead_two_is_applied_after_golden_check(env, monkeypatch) -> None:  # noqa: F811
    with _client(env, monkeypatch, '{"lookahead": 2}') as c:
        h = c.get("/healthz").json()
        assert h["status"] == "ok" and h["lookahead"] == 2
        assert h["decide_overrides"] == {"lookahead": 2}
        r = c.post("/decide", json=_req(3))
        assert r.status_code == 200
    with _client(env, monkeypatch, None) as c:
        base = c.post("/decide", json=_req(3)).json()
    # lookahead 2 may only ADD warm decisions to the bundle's own ones
    with _client(env, monkeypatch, '{"lookahead": 2}') as c:
        la2 = c.post("/decide", json=_req(3)).json()
    w1 = {d["tool_class"] for d in base["decisions"] if d["warm"]}
    w2 = {d["tool_class"] for d in la2["decisions"] if d["warm"]}
    assert w1 <= w2 and base["ranked"] == la2["ranked"]


def test_only_lookahead_may_be_overridden(env) -> None:  # noqa: F811
    b = load_bundle(read_folder(env["a"]), env["cold"])
    assert with_decide_overrides(b, {}) is b
    with pytest.raises(BundleError):
        with_decide_overrides(b, {"tau": 0.01})
    with pytest.raises(BundleError):
        with_decide_overrides(b, {"lookahead": 3})
