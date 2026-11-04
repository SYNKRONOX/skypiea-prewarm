"""Red team: isolation, determinism, output properties, reload and supply chain, code guard."""

from __future__ import annotations

import json
import random
import re
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from predictors.data import MAX_HISTORY
from services.predictor.engine import BundleError, answer, load_bundle, read_folder
from services.predictor.schema import parse_request
from tests.test_service import TOKEN

ROOT = Path(__file__).resolve().parents[2]
HDR = {"X-Admin-Token": TOKEN}
TOOLS = ["read_file", "search", "edit_file", "run_tests", "git", "shell_other", "write_file"]


def _strip(d: dict) -> str:
    d = dict(d)
    d.pop("latency_ms", None)
    return json.dumps(d, sort_keys=True)


def _session(seed: int, n: int = 30) -> dict:
    rng = random.Random(seed)
    hist = [{"tool_class": rng.choice(TOOLS), "t_start_ms": 1000.0 * i,
             "t_end_ms": 1000.0 * i + rng.uniform(0, 900), "error": rng.random() < 0.1,
             "batch_id": i} for i in range(n)]
    return {"session_id": f"sess{seed}", "now_ms": 1000.0 * n, "history": hist}


# --------------------------------------------------------------------------- isolation
def test_interleaved_sessions_from_50_threads_match_alone(client: TestClient) -> None:
    a, b = _session(1), _session(2)
    alone = {k: _strip(client.post("/decide", json=v).json()) for k, v in (("a", a), ("b", b))}
    assert alone["a"] != alone["b"]  # divergent sessions

    def call(i: int) -> tuple[str, str]:
        k, v = ("a", a) if i % 2 else ("b", b)
        return k, _strip(client.post("/decide", json=v).json())

    with ThreadPoolExecutor(50) as ex:
        for k, got in ex.map(call, range(400)):
            # identical, or a load-driven budget fallback that says so (never another
            # session's answer)
            assert got == alone[k] or json.loads(got)["degraded"] is True
            assert got != alone["b" if k == "a" else "a"]


def test_same_request_100_times_is_identical(client: TestClient) -> None:
    r = _session(7)
    outs = {_strip(client.post("/decide", json=r).json()) for _ in range(100)}
    assert len(outs) == 1


def test_output_properties_on_random_requests(env: dict) -> None:
    b = load_bundle(read_folder(env["a"]), env["cold"])
    rng = random.Random(0)
    for i in range(2000):
        d = answer(b, parse_request(_session(i, rng.randint(0, 60))))
        ps = [x["p"] for x in d["ranked"]]
        assert sum(ps) <= 1.0 + 1e-9 and ps == sorted(ps, reverse=True)
        assert all(0.0 <= p <= 1.0 for p in ps)
        for x in d["decisions"]:
            assert 0.0 <= x["prob"] <= 1.0
            if x["warm"]:
                assert 1 <= x["copies"] <= min(b.cfg.max_copies_per_tool, 10)
                assert 5 <= x["ttl_s"] <= 600
            else:
                assert x["copies"] == 0


def test_pathological_input_is_bounded_in_time(client: TestClient) -> None:
    h = _session(3, MAX_HISTORY)
    t0 = time.perf_counter()
    d = client.post("/decide", json=h).json()
    assert time.perf_counter() - t0 < 2.0 and d["latency_ms"] < 1000


# --------------------------------------------------------------------------- reload
def test_reload_under_concurrent_load_has_no_errors(client: TestClient) -> None:
    stop = threading.Event()
    codes: list[int] = []

    def hammer() -> None:
        while not stop.is_set():
            codes.append(client.post("/decide", json=_session(5)).status_code)

    ts = [threading.Thread(target=hammer) for _ in range(8)]
    for t in ts:
        t.start()
    try:
        for uri in ("bundle-b", "bundle-a", "bundle-b"):
            r = client.post("/reload", json={"model_uri": uri}, headers=HDR)
            assert r.status_code == 200, r.json()
    finally:
        stop.set()
        for t in ts:
            t.join()
    assert codes and set(codes) == {200}
    client.post("/reload", json={"model_uri": "bundle-a"}, headers=HDR)


@pytest.mark.parametrize("damage", ["empty", "huge", "wrong_version", "symlink"])
def test_more_bad_bundles(env: dict, tmp_path: Path, damage: str) -> None:
    f = tmp_path / "m"
    if damage == "empty":
        f.mkdir()
    else:
        shutil.copytree(env["a"], f)
    if damage == "huge":
        with (f / "counts.json").open("r+b") as fh:
            fh.truncate(65 * 1024 * 1024)  # sparse; over the 64 MB cap
    elif damage == "wrong_version":
        man = json.loads((f / "MANIFEST.json").read_text())
        man["model_version"] = "x" * 200
        (f / "MANIFEST.json").write_text(json.dumps(man))
    elif damage == "symlink":
        (f / "decide.yaml").unlink()
        (f / "decide.yaml").symlink_to("/etc/hostname")
    with pytest.raises(BundleError):
        load_bundle(read_folder(f), env["cold"])


def test_reload_refusals_keep_the_old_model(client: TestClient, env: dict,
                                            tmp_path_factory: pytest.TempPathFactory) -> None:
    root = env["root"]
    bad = root / "bad"
    if not bad.exists():
        shutil.copytree(env["a"], bad)
        (bad / "ranker.lgb").write_bytes(b"tampered")
    for uri, code in (("bad", 422), ("nope", 422), ("/etc", 422), ("file:///etc", 422),
                      ("s3://other-bucket/m", 422), ("bundle-a/../..", 422)):
        r = client.post("/reload", json={"model_uri": uri}, headers=HDR)
        assert r.status_code == code and r.json()["model_version"] == "bundle-a", uri
    for body in (b"{}", b"[]", b"nope", json.dumps({"model_uri": 5}).encode()):
        assert client.post("/reload", content=body, headers=HDR).status_code == 400
    assert client.post("/reload", json={"model_uri": "bundle-b"},
                       headers={"X-Admin-Token": TOKEN + "x"}).status_code == 401
    assert client.get("/healthz").json()["model_version"] == "bundle-a"


def test_double_reload_and_rollback(client: TestClient) -> None:
    for _ in range(2):
        assert client.post("/reload", json={"model_uri": "bundle-b"},
                           headers=HDR).status_code == 200
    r = client.post("/rollback", headers=HDR).json()
    assert r["model_version"] == "bundle-b" and r["previous_version"] == "bundle-b"
    client.post("/reload", json={"model_uri": "bundle-a"}, headers=HDR)
    assert client.get("/healthz").json()["model_version"] == "bundle-a"


def test_reload_during_reload_is_refused(client: TestClient) -> None:
    results: list[int] = []

    def go() -> None:
        results.append(client.post("/reload", json={"model_uri": "bundle-b"},
                                   headers=HDR).status_code)

    ts = [threading.Thread(target=go) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert 200 in results and set(results) <= {200, 409}
    client.post("/reload", json={"model_uri": "bundle-a"}, headers=HDR)


# --------------------------------------------------------------------------- code guard
def test_no_unsafe_calls_in_services_and_predictors() -> None:
    pat = re.compile(r"\b(import pickle|from pickle|cPickle|import joblib|from joblib|eval\(|"
                     r"exec\(|os\.system|shell\s*=\s*True)")
    hits = []
    for d in ("services", "predictors"):
        for p in (ROOT / d).rglob("*.py"):
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                if pat.search(line) and "re.compile" not in line:
                    hits.append(f"{p.relative_to(ROOT)}:{i}")
    assert hits == []
