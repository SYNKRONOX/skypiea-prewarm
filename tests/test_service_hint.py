"""The real predictor service and the v1.1 turn-start fields (trigger, turn): backward
compatible. A v1.1 request gets the same answer as the v1 request; an empty history (a
first-turn hint) gets a normal 200 answer."""

from __future__ import annotations

import pytest

pytest.importorskip("lightgbm")
pytest.importorskip("starlette")

from tests.test_service import _req, client, env  # noqa: E402,F401 - fixtures


def test_trigger_and_turn_do_not_change_the_answer(client) -> None:  # noqa: F811
    a = client.post("/decide", json=_req(3)).json()
    b = client.post("/decide", json=_req(3, trigger="turn_start", turn=2)).json()
    for k in ("ranked", "decisions", "mode", "model_version"):
        assert a[k] == b[k]


def test_first_turn_hint_with_empty_history(client) -> None:  # noqa: F811
    r = client.post("/decide", json={"session_id": "s" * 32, "now_ms": 0.0, "history": [],
                                     "trigger": "turn_start", "turn": 1})
    assert r.status_code == 200
    d = r.json()
    assert isinstance(d["ranked"], list) and sum(x["p"] for x in d["ranked"]) <= 1.0
