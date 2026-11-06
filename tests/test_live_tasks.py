"""Live-console tasks: vocabulary coverage (committed, generated file) and coherent mock data."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
import yaml

from agent import tasks
from agent.tasks.live import AIRLINE, CODEFIX, PROFILE_LABELS, SCRIPTS
from agent.vocab import VOCAB, coverage

ROOT = Path(__file__).resolve().parent.parent


def test_every_live_tool_is_in_its_real_profiles_vocabulary() -> None:
    vocab = json.loads(VOCAB.read_text())
    for p in ("support", "coding"):
        assert vocab["profiles"][p]["profile"] == p
        assert not vocab["profiles"][p]["synthetic"]
    rows = coverage(vocab)
    assert rows and all(r["in_vocabulary"] for r in rows), [r for r in rows
                                                              if not r["in_vocabulary"]]


def test_live_tasks_never_use_the_synthetic_profile() -> None:
    assert AIRLINE.profile == "support" and CODEFIX.profile == "coding"
    assert set(PROFILE_LABELS) == {"support", "coding"}
    assert "tau2-bench" in PROFILE_LABELS["support"]
    assert "TraceLab" in PROFILE_LABELS["coding"]


def test_gateway_classes_route_to_the_expected_servers() -> None:
    tm = yaml.safe_load((ROOT / "tools" / "tool_map.yaml").read_text())
    srv = {c: s for s, spec in tm["servers"].items() for c in spec["classes"]}
    want = {"user_lookup": "user_lookup", "reservation_lookup": "travel_booking",
            "flight_search": "travel_booking", "reservation_modify": "travel_booking",
            "reservation_cancel": "travel_booking", "escalate": "post_comment",
            "search": "search", "read_file": "read_file", "write_file": "edit_file",
            "run_tests": "run_tests", "shell_other": "shell"}
    for t in (AIRLINE, CODEFIX):
        for name in t.tool_names:
            cls, _ = t.gateway_call(name, {})
            assert srv[cls] == want[cls]


def test_fixed_args_and_aliases() -> None:
    assert AIRLINE.gateway_call("reservation_lookup", {"reservation_id": "R1"}) == (
        "reservation_lookup", {"reservation_id": "R1", "action": "lookup", "detail": True})
    assert CODEFIX.gateway_call("edit_file", {"path": "a"}) == ("write_file", {"path": "a"})
    assert CODEFIX.gateway_call("shell", {"command": "ls"})[0] == "shell_other"
    # the PRD tasks are unchanged: identity mapping
    a = tasks.get("A")
    assert a.gateway_call("order_lookup", {"order_id": "x"}) == ("order_lookup",
                                                                 {"order_id": "x"})


def test_scripts_exist_and_scripted_flows_use_known_tools() -> None:
    for name, t in (("airline", AIRLINE), ("codefix", CODEFIX)):
        assert len(SCRIPTS[name]) >= 2
        assert all(tool in t.tool_names for tool, _ in t.scripted(0))


def _run(coro):
    return asyncio.run(coro)


def test_airline_mock_data_is_coherent_across_calls() -> None:
    pytest.importorskip("mcp")
    pytest.importorskip("pandas")
    from tools.travel_booking.server import travel_booking
    from tools.user_lookup.server import user_lookup

    u = _run(user_lookup(user_id="priya_menon_1119", with_reservations=True, service_ms=0))
    rid = u["user"]["reservations"][0]
    r = _run(travel_booking(action="lookup", reservation_id=rid, detail=True, service_ms=0))
    assert r["user_id"] == "priya_menon_1119" and r["reservation_id"] == rid
    s = _run(travel_booking(action="search", origin=r["origin"],
                            destination=r["destination"], service_ms=0))
    other = next(f for f in s["flights"] if f["flight_no"] != r["flight_no"])
    m = _run(travel_booking(action="modify", reservation_id=rid, detail=True,
                            flight_no=other["flight_no"], service_ms=0))
    assert m["accepted"] and m["new_flight_no"] == other["flight_no"]
    assert (m["new_origin"], m["new_destination"]) == (r["origin"], r["destination"])


def test_tool_defaults_unchanged() -> None:
    pytest.importorskip("mcp")
    pytest.importorskip("pandas")
    from tools.travel_booking.server import travel_booking
    from tools.user_lookup.server import user_lookup

    u = _run(user_lookup(user_id="priya_menon_1119", service_ms=0))
    assert "reservations" not in u["user"]
    r = _run(travel_booking(action="lookup", reservation_id="R35033531", service_ms=0))
    assert set(r) == {"action", "reservation_id", "flight_no", "cabin", "passengers", "bags"}
