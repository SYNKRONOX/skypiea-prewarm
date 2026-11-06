"""Turn-start prewarm hint (POST /v1/hint): off by default, metadata only, backward compatible."""

from __future__ import annotations

import pytest

pytest.importorskip("httpx", reason="make setup")
pytest.importorskip("starlette", reason="make setup")

import asyncio  # noqa: E402

import httpx  # noqa: E402

from gateway.contract import DecideRequest, HistoryItem  # noqa: E402
from gateway.predictor import FakePredictor  # noqa: E402
from tests.test_gateway import GOOD, call, counter, make, post  # noqa: E402

FIRST = {"ranked": [{"tool_class": "user_lookup", "p": 0.5}],
         "decisions": [{"tool_class": "user_lookup", "warm": True, "copies": 1,
                        "start_in_ms": 0, "ttl_s": 30, "prob": 0.5}],
         "model_version": "fake-1", "latency_ms": 1.0}


async def hint(gw, body, sid: str | None = "s1"):
    h = {"content-type": "application/json", **({"Mcp-Session-Id": sid} if sid else {})}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(gw.app(background=False)),
                                 base_url="http://gw") as c:
        r = await c.post("/v1/hint", json=body, headers=h)
    await gw.drain()
    return r


def test_hint_is_off_by_default_and_changes_nothing() -> None:
    pred = FakePredictor(GOOD)
    gw, ctrl, _, _ = make(pred, ready={"policy_check": 1, "user_lookup": 1})
    r = asyncio.run(hint(gw, {"turn": 1}))
    assert r.status_code == 404
    assert pred.requests == [] and ctrl.warms == []
    r = asyncio.run(post(gw, call("user_lookup")))
    assert "X-Skypiea-Warm-Source" not in r.headers
    # v1 requests carry no trigger/turn at all
    assert "trigger" not in pred.requests[0].to_json()
    assert "turn" not in pred.requests[0].to_json()


def test_first_turn_hint_asks_with_empty_history_and_warms() -> None:
    pred = FakePredictor(FIRST)
    gw, ctrl, _, _ = make(pred, ready={"user_lookup": 1}, turn_hint=True)
    r = asyncio.run(hint(gw, {"turn": 1}, sid="abc"))
    assert r.status_code == 202 and r.headers["Mcp-Session-Id"] == "abc"
    (req,) = pred.requests
    j = req.to_json()
    assert j["trigger"] == "turn_start" and j["turn"] == 1 and j["history"] == []
    assert j["now_ms"] == 0.0 and j["session_id"] == "abc"
    assert ctrl.warms == [("user_lookup", 1, 30, "hint:fake-1")]
    assert counter(gw.metrics, "skypiea_gateway_hints_total") == 1
    assert counter(gw.metrics, "skypiea_gateway_hint_warms_total", tool="user_lookup") == 1
    # the next call was predicted by the hint and says so
    r = asyncio.run(post(gw, call("user_lookup"), sid="abc"))
    assert r.headers["X-Skypiea-Predicted"] == "1"
    assert r.headers["X-Skypiea-Warm-Source"] == "hint"
    s = gw.sessions._s["abc"]
    assert s.calls[0]["warm_source"] == "hint"
    # the session clock restarts at the first call (same as without a hint)
    assert s.calls[0]["t_start_ms"] == 0.0


def test_later_turn_hint_sends_history_and_after_call_source_is_call() -> None:
    pred = FakePredictor(GOOD)
    gw, _, _, _ = make(pred, ready={"policy_check": 1, "order_lookup": 1}, turn_hint=True)
    asyncio.run(post(gw, call("order_lookup"), sid="s2"))
    asyncio.run(hint(gw, {"turn": 2}, sid="s2"))
    req = pred.requests[-1]
    assert req.trigger == "turn_start" and req.turn == 2
    assert [h.tool_class for h in req.history] == ["order_lookup"]
    r = asyncio.run(post(gw, call("policy_check"), sid="s2"))
    assert r.headers["X-Skypiea-Warm-Source"] == "hint"
    r = asyncio.run(post(gw, call("policy_check"), sid="s2"))     # predicted after a call
    assert r.headers["X-Skypiea-Warm-Source"] == "call"


@pytest.mark.parametrize("body", [{"turn": 1, "text": "hello"}, {"message": "x"},
                                  {"turn": "1"}, {"turn": True}, [1]])
def test_hint_refuses_anything_but_the_turn_number(body) -> None:
    pred = FakePredictor(GOOD)
    gw, ctrl, _, _ = make(pred, turn_hint=True)
    r = asyncio.run(hint(gw, body))
    assert r.status_code == 400
    assert pred.requests == [] and ctrl.warms == []


def test_hint_needs_a_session_header() -> None:
    gw, _, _, _ = make(FakePredictor(GOOD), turn_hint=True)
    assert asyncio.run(hint(gw, {"turn": 1}, sid=None)).status_code == 400


def test_dead_predictor_on_hint_is_ignored_and_calls_still_work() -> None:
    pred = FakePredictor(fail=httpx.ConnectError("down"))
    gw, ctrl, _, _ = make(pred, ready={"user_lookup": 1}, turn_hint=True)
    assert asyncio.run(hint(gw, {"turn": 1})).status_code == 202
    assert ctrl.warms == []
    r = asyncio.run(post(gw, call("user_lookup")))
    assert "result" in r.json()


def test_contract_roundtrip_with_and_without_trigger() -> None:
    h = [HistoryItem("a", 0.0, 1.0, False, 0)]
    old = DecideRequest("s", 1.0, h)
    assert DecideRequest.from_json(old.to_json()) == old
    new = DecideRequest("s", 1.0, h, trigger="turn_start", turn=3)
    assert DecideRequest.from_json(new.to_json()) == new

