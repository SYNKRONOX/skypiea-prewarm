"""Issue #14 (decision P3): the gateway sends at most the last HISTORY_WINDOW calls plus
history_total, so a session past 1,000 calls keeps getting predictions (no 413)."""

from __future__ import annotations

import asyncio

from gateway import app as gw_app
from gateway.contract import DecideRequest, HistoryItem
from gateway.predictor import FakePredictor
from gateway.predictor_stub import StubPredictor
from tests.test_gateway import GOOD, call, make, post


def test_long_session_sends_a_bounded_window_with_history_total():
    async def go():
        pred = FakePredictor(GOOD)
        gw, ctrl, _, _ = make(predictor=pred, ready={"shell": 1, "policy_check": 1})
        s = gw.sessions.get("s1")
        for i in range(1100):                                  # 1,100 earlier calls
            s.record(HistoryItem("shell", i, i + 1, False, i))
        r = await post(gw, call("shell"))
        assert r.status_code == 200
        req = pred.requests[-1]
        assert len(req.history) == gw_app.HISTORY_WINDOW
        assert req.history_total == 1101
        assert req.to_json()["history_total"] == 1101
        assert len(s.history) <= gw_app.HISTORY_WINDOW         # memory bounded too
        assert ctrl.warms                                      # prewarming still happens
    asyncio.run(go())


def test_short_session_unchanged():
    async def go():
        pred = FakePredictor(GOOD)
        gw, _, _, _ = make(predictor=pred, ready={"shell": 1})
        await post(gw, call("shell"))
        req = pred.requests[-1]
        assert len(req.history) == 1 and req.history_total == 1
    asyncio.run(go())


def test_contract_round_trips_history_total():
    req = DecideRequest("s", 1.0, [HistoryItem("a", 0, 1, False, 0)], history_total=5)
    assert DecideRequest.from_json(req.to_json()).history_total == 5
    assert "history_total" not in DecideRequest("s", 1.0, []).to_json()


def test_stub_keeps_learning_past_the_window():
    stub = StubPredictor("markov1", {})
    seq = ["a", "b"] * 300
    for n in range(1, len(seq) + 1):
        lo = max(0, n - 200)
        hist = [HistoryItem(t, i, i + 1, False, i) for i, t in enumerate(seq[lo:n], start=lo)]
        stub.decide(DecideRequest("s", float(n), hist, history_total=n))
    assert sum(stub.bi["a"].values()) == 300          # every a->b transition learned once
