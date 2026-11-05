"""Red team: hostile requests to /decide (docs/redteam.md, "Input attacks")."""

from __future__ import annotations

import json
import random
import resource

import pytest
from starlette.testclient import TestClient

from tests.test_service import _req

ATTACK_NAMES = ["", "x" * 10_000, "réad_fïle", "read\x00file", "a\nb", "'; DROP TABLE x;--",
                "$(rm -rf /)", "%s%s%n", "../../etc/passwd", "__proto__", "constructor",
                "never_seen_tool", "READ_FILE"]


def _ok(r) -> dict:  # noqa: ANN001
    assert r.status_code == 200, r.status_code
    d = r.json()
    assert isinstance(d["ranked"], list) and isinstance(d["decisions"], list)
    assert sum(x["p"] for x in d["ranked"]) <= 1.0 + 1e-9
    return d


def _no_echo(text: str, raw: str) -> None:
    if len(raw) >= 4 and raw.lower() not in ("read_file",):
        assert raw not in text


@pytest.mark.parametrize("body", [b"", b"{", b"null", b"\"str\"", b"123", b"[]", b"{}",
                                  b"\xff\xfe", b'{"session_id": "a", "history": [}'])
def test_invalid_json_and_shapes(client: TestClient, body: bytes) -> None:
    r = client.post("/decide", content=body)
    assert r.status_code in (200, 400)
    if r.status_code == 200:
        assert r.json()["decisions"] == []


def test_wrong_types_missing_and_extra_fields(client: TestClient) -> None:
    for bad in ({"session_id": "s", "history": {"a": 1}}, {"session_id": ["s"], "history": []},
                {"session_id": "s"}, {"history": []}, {"session_id": "s", "history": [1, "x",
                                                                                   None]}):
        _ok(client.post("/decide", json=bad))
    d = _ok(client.post("/decide", json={**_req(), "extra": {"deep": [1, 2, 3]},
                                         "__proto__": {"admin": True}}))
    assert "extra" not in json.dumps(d)


def test_json_bombs_and_huge_bodies(client: TestClient) -> None:
    assert client.post("/decide", content=b"[" * 50_000 + b"]" * 50_000).status_code in (400,
                                                                                        413)
    nested = b'{"session_id":"s","history":' + b"[" * 5000 + b"]" * 5000 + b"}"
    assert client.post("/decide", content=nested).status_code in (200, 400)
    assert client.post("/decide", content=b"{" + b" " * (10 * 1024 * 1024) + b"}"
                       ).status_code == 413
    many = {**_req(), "history": _req(1)["history"] * 100_000}
    assert client.post("/decide", content=json.dumps(many).encode()).status_code == 413
    # a lying Content-Length is refused before reading
    r = client.post("/decide", content=b"{}", headers={"content-length": "999999999"})
    assert r.status_code in (400, 413)


@pytest.mark.parametrize("n", [0, 1, 1000])
def test_history_sizes(client: TestClient, n: int) -> None:
    h = _req(1)["history"][0]
    d = _ok(client.post("/decide", json={**_req(), "history": [h] * n}))
    assert d["truncated"] == (n > 200)


@pytest.mark.parametrize("name", ATTACK_NAMES)
def test_tool_name_attacks_never_echo(client: TestClient, name: str) -> None:
    d = _req(3)
    d["history"][1]["tool_class"] = name
    r = client.post("/decide", json=d)
    _ok(r)
    _no_echo(r.text, name)


@pytest.mark.parametrize("v", [float("nan"), float("inf"), -float("inf"), -5.0, 1e300, "12",
                               "1e3", None, True, [1]])
def test_number_attacks(client: TestClient, v: object) -> None:
    for field in ("t_start_ms", "t_end_ms", "batch_id"):
        d = _req(3)
        d["history"][1][field] = v
        _ok(client.post("/decide", content=json.dumps({**d, "now_ms": v}, allow_nan=True)))


def test_decreasing_duplicate_times_and_ids(client: TestClient) -> None:
    d = _req(4)
    for i, h in enumerate(d["history"]):
        h["t_start_ms"], h["t_end_ms"], h["batch_id"] = 1000.0 - i, 900.0, [3, 3, 1, 0][i]
    _ok(client.post("/decide", json=d))


@pytest.mark.parametrize("sid", ["", "a b", "a/b", "é", "x" * 10_000, "../x", "a\nb", "%00"])
def test_session_id_attacks(client: TestClient, sid: str) -> None:
    r = client.post("/decide", json={**_req(), "session_id": sid})
    d = _ok(r)
    assert d["decisions"] == [] and d["degraded"] is True
    _no_echo(r.text, sid)


def test_many_unique_sessions_keep_memory_flat(client: TestClient) -> None:
    def rss_kb() -> int:
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    for i in range(500):  # warm-up
        client.post("/decide", json={**_req(), "session_id": f"w{i}"})
    before = rss_kb()
    rng = random.Random(0)
    for i in range(10_000):
        client.post("/decide", json={**_req(rng.randint(0, 4)), "session_id": f"s{i:06d}x"})
    assert rss_kb() - before < 50_000  # peak RSS grows < 50 MB over 10,000 ids


def test_robustness_histories(client: TestClient) -> None:
    unseen = {**_req(), "history": [{**h, "tool_class": "zz_unseen"} for h in _req(4)["history"]]}
    errs = {**_req(), "history": [{**h, "error": True} for h in _req(4)["history"]]}
    for d in (unseen, errs, _req(0), {**_req(), "history": _req(4)["history"] * 50}):
        _ok(client.post("/decide", json=d))
