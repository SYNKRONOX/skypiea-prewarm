"""Issue #12: the predictor contract bounds start_in_ms, ttl_s and the number of decisions, so a
contract-valid answer cannot park thousands of long-sleeping warm tasks in the gateway (FR-4:
off-contract answers are counted and ignored)."""

from __future__ import annotations

import asyncio

import pytest

from gateway import contract
from gateway.contract import ContractError, parse_response
from gateway.predictor import FakePredictor
from tests.test_gateway import call, make, post


def ans(**dec):
    d = {"tool_class": "shell", "warm": True, "copies": 1, "start_in_ms": 0, "ttl_s": None,
         "prob": 0.5, **dec}
    return {"ranked": [], "decisions": [d], "model_version": "m", "latency_ms": 0.0}


@pytest.mark.parametrize("dec", [{"start_in_ms": 10**12}, {"start_in_ms": 600_001},
                                 {"ttl_s": 10**12}, {"ttl_s": 86_401}])
def test_out_of_range_is_off_contract(dec):
    with pytest.raises(ContractError):
        parse_response(ans(**dec))


def test_in_range_still_accepted():
    r = parse_response(ans(start_in_ms=600_000, ttl_s=86_400))
    assert r.decisions[0].start_in_ms == 600_000


def test_too_many_decisions_is_off_contract():
    a = ans()
    a["decisions"] = a["decisions"] * (contract.MAX_DECISIONS + 1)
    with pytest.raises(ContractError):
        parse_response(a)


def test_gateway_spawns_no_long_sleepers_for_a_huge_start():
    async def go():
        gw, ctrl, _, _ = make(predictor=FakePredictor(ans(start_in_ms=10**12)),
                              ready={"shell": 1})
        for i in range(20):
            await post(gw, call("shell", i))
        assert ctrl.warms == [] and len(gw._tasks) == 0
    asyncio.run(go())
