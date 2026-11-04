"""Predictor service (services/predictor/): request hardening, bundle checks, /decide, reload."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml
from starlette.testclient import TestClient

from predictors.cold_start import ColdStarts
from predictors.data import episodes
from services.predictor.app import Settings, make_app
from services.predictor.engine import BundleError, load_bundle, read_folder
from services.predictor.export import build
from services.predictor.schema import UNK, TooLarge, parse_request
from traces.load import SAMPLES, load_sessions

TOKEN = "t0ken-for-tests"


def _cold(path: Path) -> Path:
    def srv(tier: str, classes: list[str], ms: float) -> dict:
        m = {"n": 5, "median_ms": ms, "p95_ms": ms + 400.0}
        return {"tier": tier, "classes": classes, "cached": m, "uncached": dict(m)}
    d = {"schema_version": 1, "environment": "test", "status": "fixture", "fallback": "generic",
         "servers": {"shell": srv("light", ["shell_other", "run_tests", "git"], 1500.0),
                     "generic": srv("heavy", ["other"], 3000.0)}}
    path.write_text(yaml.safe_dump(d))
    return path


@pytest.fixture(scope="module")
def env(tmp_path_factory: pytest.TempPathFactory) -> dict:
    root = tmp_path_factory.mktemp("models")
    cold_path = _cold(root.parent / "cold.yaml")
    cold = ColdStarts.load(cold_path)
    tr, va = (episodes(load_sessions("tracelab", s, root=SAMPLES)) for s in ("train", "val"))
    a = build(tr, va, root / "bundle-a", cold, "tracelab")
    b = root / "bundle-b"
    shutil.copytree(a, b)
    man = json.loads((b / "MANIFEST.json").read_text())
    man["model_version"] = "bundle-b"
    (b / "MANIFEST.json").write_text(json.dumps(man))
    return {"root": root, "cold": cold, "cold_path": cold_path, "a": a, "b": b, "val": va}


@pytest.fixture
def client(env: dict) -> TestClient:
    s = Settings(str(env["a"]), str(env["cold_path"]), TOKEN, str(env["root"]), ())
    with TestClient(make_app(s)) as c:
        yield c


def _req(n: int = 3, **kw: object) -> dict:
    hist = [{"tool_class": t, "t_start_ms": 1000.0 * i, "t_end_ms": 1000.0 * i + 50.0,
             "error": False, "batch_id": i}
            for i, t in enumerate(["read_file", "search", "edit_file", "run_tests"][:n])]
    return {"session_id": "a" * 32, "now_ms": 5000.0, "history": hist, **kw}


# --------------------------------------------------------------------------- schema
def test_parse_repairs_and_flags() -> None:
    d = _req(4)
    d["history"][1]["tool_class"] = "Rm -rf /"
    d["history"][2]["t_start_ms"] = float("nan")
    d["history"][2]["batch_id"] = 0  # decreasing
    d["history"][0]["error"] = "yes"
    p = parse_request(d)
    assert p.bad is None and [e.tool for e in p.events][1] == UNK
    assert [e.batch_id for e in p.events] == [0, 1, 2, 3]
    assert p.events[2].t_offset_ms == 0.0
    assert {"tool_class", "t_start_ms", "batch_id", "error"} <= set(p.flags)
    same = parse_request({**_req(), "history": [{**h, "batch_id": 7} for h in _req()["history"]]})
    assert {e.batch_id for e in same.events} == {0}


def test_parse_limits_and_bad_requests() -> None:
    long = {**_req(), "history": _req(1)["history"] * 300}
    p = parse_request(long)
    assert p.truncated and len(p.events) == 200
    with pytest.raises(TooLarge):
        parse_request({**_req(), "history": _req(1)["history"] * 1001})
    assert parse_request({**_req(), "session_id": "x" * 65}).bad == "session_id"
    assert parse_request({**_req(), "session_id": "a/b"}).bad == "session_id"
    assert parse_request([1, 2]).bad == "not_object"
    assert parse_request({**_req(), "history": "no"}).bad == "history"
    assert parse_request({**_req(), "history_total": 2}).history_total is None
    assert parse_request({**_req(), "history_total": 999}).history_total == 999


# --------------------------------------------------------------------------- bundle
def test_bundle_round_trip_is_deterministic(env: dict, tmp_path: Path) -> None:
    b = load_bundle(read_folder(env["a"]), env["cold"])
    assert b.version == "bundle-a" and b.golden
    tr, va = (episodes(load_sessions("tracelab", s, root=SAMPLES)) for s in ("train", "val"))
    again = build(tr, va, tmp_path / "bundle-a", env["cold"], "tracelab")
    for n in ("ranker.lgb", "counts.json", "golden.json", "MANIFEST.json"):
        assert (again / n).read_bytes() == (env["a"] / n).read_bytes(), n


@pytest.mark.parametrize("damage", ["tamper", "truncate", "missing", "schema", "vocab"])
def test_bad_bundles_are_refused(env: dict, tmp_path: Path, damage: str) -> None:
    f = tmp_path / "m"
    shutil.copytree(env["a"], f)
    if damage == "tamper":
        raw = bytearray((f / "ranker.lgb").read_bytes())
        raw[len(raw) // 2] ^= 1
        (f / "ranker.lgb").write_bytes(bytes(raw))
    elif damage == "truncate":
        (f / "counts.json").write_bytes((f / "counts.json").read_bytes()[:100])
    elif damage == "missing":
        (f / "decide.yaml").unlink()
    else:
        man = json.loads((f / "MANIFEST.json").read_text())
        if damage == "schema":
            man["schema_version"] = 99
        else:
            man["vocabulary"] = [*man["vocabulary"], "made_up_tool"]
        (f / "MANIFEST.json").write_text(json.dumps(man))
    with pytest.raises(BundleError):
        load_bundle(read_folder(f), env["cold"])


# --------------------------------------------------------------------------- /decide
def test_decide_answers_follow_the_contract(client: TestClient) -> None:
    r = client.post("/decide", json=_req(4, oracle=[{"tool_class": "x", "in_ms": 1.0}]))
    assert r.status_code == 200
    d = r.json()
    assert d["model_version"] == "bundle-a" and d["mode"] in ("ranker", "prior")
    assert len(d["ranked"]) >= 3 and sum(x["p"] for x in d["ranked"]) <= 1.0
    assert [x["p"] for x in d["ranked"]] == sorted((x["p"] for x in d["ranked"]), reverse=True)
    for x in d["decisions"]:
        assert isinstance(x["warm"], bool) and 0 <= x["copies"] <= 10
        assert isinstance(x["start_in_ms"], int) and x["start_in_ms"] >= 0
        assert x["ttl_s"] is None or (isinstance(x["ttl_s"], int) and 0 <= x["ttl_s"] <= 600)
        assert 0.0 <= x["prob"] <= 1.0
        assert x["warm"] == (x["copies"] > 0)
    assert isinstance(d["keep_after_use_s"], int) and d["truncated"] is False
    assert client.post("/decide", json=_req(4)).json()["ranked"] == d["ranked"]


def test_decide_fails_open(client: TestClient) -> None:
    assert client.post("/decide", content=b"{not json").status_code == 400
    assert client.post("/decide", content=b"[" * 100_000 + b"]" * 100_000).status_code in (
        400, 413)
    assert client.post("/decide", content=b"x" * (300 * 1024)).status_code == 413
    for bad in ({"session_id": "a" * 99, "history": []}, {"history": []}, [], {"session_id": 1}):
        r = client.post("/decide", json=bad)
        assert r.status_code == 200
        assert r.json()["decisions"] == [] and r.json()["degraded"] is True
    empty = client.post("/decide", json=_req(0))
    assert empty.status_code == 200 and "decisions" in empty.json()


def test_health_and_metrics_carry_no_session_data(client: TestClient) -> None:
    marker = "zzmarker0123456789"
    client.post("/decide", json={**_req(), "session_id": marker})
    client.post("/decide", json={**_req(), "history": [{"tool_class": marker}]})
    h = client.get("/healthz")
    assert h.status_code == 200 and h.json()["model_version"] == "bundle-a"
    m = client.get("/metrics").text
    assert "skypiea_predictor_requests_total" in m and marker not in m


# --------------------------------------------------------------------------- reload
def test_reload_rollback_and_refusals(client: TestClient, env: dict) -> None:
    hdr = {"X-Admin-Token": TOKEN}
    assert client.post("/reload", json={"model_uri": "bundle-b"}).status_code == 401
    assert client.post("/reload", json={"model_uri": "bundle-b"},
                       headers={"X-Admin-Token": "wrong"}).status_code == 401
    for uri in ("../", "/etc", "file:///etc/passwd", "https://example.com/m", "s3://x/y",
                "bundle-a/../../"):
        r = client.post("/reload", json={"model_uri": uri}, headers=hdr)
        assert r.status_code == 422 and r.json()["model_version"] == "bundle-a", uri
    r = client.post("/reload", json={"model_uri": "bundle-b"}, headers=hdr)
    assert r.status_code == 200 and r.json()["model_version"] == "bundle-b"
    assert r.json()["seconds"] < 2.0
    assert client.post("/decide", json=_req()).json()["model_version"] == "bundle-b"
    r = client.post("/rollback", headers=hdr)
    assert r.json()["model_version"] == "bundle-a"
    assert client.post("/decide", json=_req()).json()["model_version"] == "bundle-a"


def test_admin_disabled_without_token(env: dict) -> None:
    s = Settings(str(env["a"]), str(env["cold_path"]), None, str(env["root"]), ())
    with TestClient(make_app(s)) as c:
        assert c.post("/reload", json={"model_uri": "bundle-b"},
                      headers={"X-Admin-Token": ""}).status_code == 403
        assert c.post("/rollback").status_code == 403


def test_fuzzed_requests_never_crash(client: TestClient) -> None:
    from hypothesis import HealthCheck, given, settings
    from hypothesis import strategies as st

    scalar = st.one_of(st.none(), st.booleans(), st.integers(-10**12, 10**12),
                       st.floats(allow_nan=True, allow_infinity=True), st.text(max_size=80))
    item = st.dictionaries(st.sampled_from(["tool_class", "t_start_ms", "t_end_ms", "error",
                                            "batch_id", "extra"]), scalar, max_size=6)
    body = st.fixed_dictionaries({"session_id": st.one_of(st.just("s" * 32), scalar),
                                  "history": st.one_of(st.lists(item, max_size=40), scalar)},
                                 optional={"now_ms": scalar, "history_total": scalar})

    @settings(max_examples=150, deadline=None, suppress_health_check=list(HealthCheck))
    @given(body)
    def check(d: dict) -> None:
        r = client.post("/decide", content=json.dumps(d, allow_nan=True))
        assert r.status_code in (200, 400)
        if r.status_code == 200:
            out = r.json()
            assert sum(x["p"] for x in out["ranked"]) <= 1.0
            assert all(0 <= x["copies"] <= 10 for x in out["decisions"])

    check()
