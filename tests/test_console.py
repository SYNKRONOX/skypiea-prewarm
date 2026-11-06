"""Live Console, offline: engine (mirrored calls, records, totals, replay plan, CSV), poller
(pod states, contention), predictor evaluation, text tool-call recovery, and the web app's
routes. Fake gateways (httpx.MockTransport) and a scripted LLM: no cluster, no network."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import time

import httpx
import pytest

pytest.importorskip("starlette")

from agent.mcp_client import SESSION_HEADER, McpClient  # noqa: E402
from agent.tasks.live import AIRLINE  # noqa: E402
from console.engine import (  # noqa: E402
    CSV_FIELDS,
    Conversation,
    evaluate_decisions,
    pricing,
    replay_plan,
    to_csv,
    totals,
)
from console.llm_stream import ScriptedStreamingLLM, tool_call_from_text  # noqa: E402
from console.poller import Poller  # noqa: E402


# ---------------------------------------------------------------- fakes
class FakeGW:
    """Gateway double: first call of a class is cold (2500 ms added, and `cold_s` of real
    waiting); records arguments and request times."""

    def __init__(self, name: str, result_extra: dict | None = None, cold_s: float = 0.0):
        self.name, self.seen, self.warm = name, [], set()
        self.extra = result_extra or {}
        self.cold_s, self.times = cold_s, []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        if req.method == "DELETE":
            return httpx.Response(204)
        body = json.loads(req.content)
        name = body["params"]["name"]
        self.seen.append((req.headers.get(SESSION_HEADER), name, body["params"]["arguments"]))
        cold = name not in self.warm
        self.warm.add(name)
        t0 = time.monotonic()
        if cold and self.cold_s:
            time.sleep(self.cold_s)
        self.times.append((t0, time.monotonic()))
        return httpx.Response(200, headers={
            SESSION_HEADER: req.headers.get(SESSION_HEADER, "x"),
            "X-Skypiea-Cold": "1" if cold else "0",
            "X-Skypiea-Added-Ms": "2500" if cold else "0", "X-Skypiea-Held-Ms": "150",
            "X-Skypiea-Predicted": "0"}, json={
            "jsonrpc": "2.0", "id": body["id"],
            "result": {"structuredContent": {"tool": name, **self.extra}}})


class FakeStack:
    def __init__(self, key: str):
        from console.stacks import BASE, PRED
        self.s = {"base": BASE, "pred": PRED}[key]
        self.hints, self.policies = [], []

    async def hint(self, sid, turn):
        self.hints.append((sid, turn))
        return 202

    async def session(self, sid):
        return {"calls": [], "decisions": []}

    async def state(self):
        return {"user_lookup": {"live": 0, "ready": 0, "queued": 0, "warm_for_s": 0}}

    async def idle_pod_s(self):
        return {}

    async def policy(self):
        return {"name": "scale_to_zero"}

    async def set_policy(self, key):
        self.policies.append(key)


class NullPoller:
    idle: dict = {"base": {}, "pred": {}}

    def contention(self, t0, t1):
        return {"max_starting": 1, "samples": 3}


def factory(gws):
    def make(url):
        gw = gws["base"] if ":18180" in url else gws["pred"]
        return McpClient(url, client=httpx.Client(transport=httpx.MockTransport(gw)))
    return make


def run_conversation(mirror="concurrent", hint=False, extra_pred=None):
    gws = {"base": FakeGW("base"), "pred": FakeGW("pred", extra_pred)}
    stacks = {"base": FakeStack("base"), "pred": FakeStack("pred")}
    events: list = []

    async def publish(ev):
        events.append(ev)

    llm = ScriptedStreamingLLM([[("user_lookup", {"user_id": "priya_menon_1119"}),
                                 ("reservation_lookup", {"reservation_id": "R35033531"})],
                                [("flight_search", {"origin": "GOI", "destination": "HYD"})]])
    settings = {"mirror": mirror, "hint": hint, "baseline_policy": "scale_to_zero"}
    conv = Conversation(AIRLINE, llm, stacks, NullPoller(), publish, settings,
                        mcp_factory=factory(gws))

    async def go():
        await conv.user_turn("hello", "autopilot")
        await conv.user_turn("again", "presenter")
        await asyncio.sleep(0.5)       # let predictor refreshes finish
    asyncio.run(go())
    return conv, gws, stacks, events


# ---------------------------------------------------------------- engine
def test_every_call_goes_to_both_stacks_with_same_args_and_session() -> None:
    conv, gws, _, _ = run_conversation()
    assert [s[1:] for s in gws["base"].seen] == [s[1:] for s in gws["pred"].seen]
    assert {s[0] for s in gws["base"].seen} == {conv.sid} == {s[0] for s in gws["pred"].seen}
    # gateway classes and fixed args (coherent airline data)
    assert gws["pred"].seen[1][1:] == ("reservation_lookup", {
        "reservation_id": "R35033531", "action": "lookup", "detail": True})
    assert len(conv.calls) == 3 and all(c["results_match"] for c in conv.calls)


def test_records_hold_per_stack_metadata_and_no_payloads() -> None:
    conv, _, _, _ = run_conversation()
    c = conv.calls[0]
    for k in ("base", "pred"):
        assert c[f"{k}_cold"] is True and c[f"{k}_added_ms"] == 2500.0
        assert c[f"{k}_held_ms"] == 150.0 and c[f"{k}_ok"] is True
        assert c[f"{k}_contention_max_starting"] == 1
    assert c["turn"] == 1 and c["reply"] == 1 and c["pos_in_reply"] == 0
    assert conv.calls[2]["turn"] == 2
    text = to_csv(conv.calls)
    assert "priya" not in text and "R35033531" not in text and "hello" not in text
    rows = list(csv.DictReader(io.StringIO(text)))
    assert list(rows[0]) == CSV_FIELDS and len(rows) == 3


def test_mismatch_between_stacks_is_flagged() -> None:
    conv, _, _, _ = run_conversation(extra_pred={"x": 1})
    assert not any(c["results_match"] for c in conv.calls)


def test_hint_only_when_on_and_only_to_the_predictive_stack() -> None:
    _, _, stacks, ev = run_conversation(hint=False)
    assert stacks["pred"].hints == [] and stacks["base"].hints == []
    conv, _, stacks, ev = run_conversation(hint=True)
    assert stacks["pred"].hints == [(conv.sid, 1), (conv.sid, 2)]
    assert stacks["base"].hints == []
    assert [e["turn"] for e in ev if e["type"] == "hint"] == [1, 2]


def test_sequential_mode_calls_predictive_first() -> None:
    order = []

    class Tracking(FakeGW):
        def __call__(self, req):
            if req.method != "DELETE":
                order.append(self.name)
            return super().__call__(req)

    gws = {"base": Tracking("base"), "pred": Tracking("pred")}

    async def publish(ev):
        pass
    conv = Conversation(AIRLINE, ScriptedStreamingLLM([[("user_lookup", {})]]),
                        {"base": FakeStack("base"), "pred": FakeStack("pred")}, NullPoller(),
                        publish, {"mirror": "sequential", "hint": False,
                                  "baseline_policy": "scale_to_zero"},
                        mcp_factory=factory(gws))
    asyncio.run(conv.user_turn("x"))
    assert order == ["pred", "base"]


def test_totals_and_cost_from_records() -> None:
    calls = [{"base_ok": True, "base_wall_ms": 3000.0, "base_added_ms": 2500.0,
              "base_cold": True, "pred_ok": True, "pred_wall_ms": 100.0, "pred_added_ms": 0.0,
              "pred_cold": False},
             {"base_ok": True, "base_wall_ms": 50.0, "base_added_ms": 0.0, "base_cold": False,
              "pred_ok": False, "pred_wall_ms": 9.0, "pred_added_ms": None, "pred_cold": None}]
    price = pricing()
    t = totals(calls, {"base": {"user_lookup": 10.0}, "pred": {}}, price)
    assert t["base"]["cold"] == 1 and t["base"]["wait_ms"] == 3050.0
    assert t["base"]["added_p50_ms"] == 0.0 and t["base"]["added_p95_ms"] == 2500.0
    assert t["base"]["n_added"] == 2 and t["pred"]["ok"] == 1 and t["pred"]["n_added"] == 1
    assert t["base"]["idle_cost_usd"] == pytest.approx(
        10.0 * price["cores"]["user_lookup"] * price["vcpu_second_usd"])
    assert price["status"] == "assumption"


def test_replay_plan_uses_measured_gaps() -> None:
    calls = [{"tool_class": "a", "pred_t_s": 1.0, "pred_wall_ms": 500.0, "turn": 1},
             {"tool_class": None, "turn": 1},
             {"tool_class": "b", "pred_t_s": 4.0, "pred_wall_ms": 100.0, "turn": 1}]
    plan = replay_plan(calls, {0: {"x": 1}})
    assert [p["_gap_s"] for p in plan] == [0.0, 2.5]
    assert plan[0]["_args"] == {"x": 1}


def test_evaluate_decisions_against_the_next_call() -> None:
    sess = {"calls": [{"tool_class": "user_lookup", "cold": True},
                      {"tool_class": "reservation_lookup", "cold": False}],
            "decisions": [
                {"after_call": -1, "trigger": "turn_start", "turn": 1, "outcome": "ok",
                 "ranked": [{"tool_class": "user_lookup", "p": 0.5}],
                 "decisions": [{"tool_class": "user_lookup", "warm": True, "copies": 1}]},
                {"after_call": 0, "outcome": "ok",
                 "ranked": [{"tool_class": "flight_search", "p": 0.6},
                            {"tool_class": "reservation_lookup", "p": 0.3}],
                 "decisions": [{"tool_class": "reservation_lookup", "warm": True, "copies": 1}]},
                {"after_call": 1, "outcome": "error:timeout"}]}
    e = evaluate_decisions(sess)
    assert e[0]["trigger"] == "turn_start" and e[0]["top1_right"] and e[0]["warmed_right"]
    assert not e[1]["top1_right"] and e[1]["warmed_right"]
    assert e[2]["next_tool"] is None and e[2]["outcome"] == "error:timeout"


# ---------------------------------------------------------------- llm text recovery
def test_tool_call_written_as_text_is_recovered_and_flagged() -> None:
    names = {"reservation_lookup", "flight_search"}
    got = tool_call_from_text('Next I will look it up.\n{"name": "reservation_lookup", '
                              '"parameters": {"reservation_id": "R1"}}', names)
    assert got is not None
    tc, before = got
    assert tc.name == "reservation_lookup" and tc.arguments == {"reservation_id": "R1"}
    assert before == "Next I will look it up."
    assert tool_call_from_text('{"name": "rm_rf", "parameters": {}}', names) is None
    assert tool_call_from_text("no json here {", names) is None


# ---------------------------------------------------------------- poller
def test_poller_states_warm_time_and_contention() -> None:
    t = [0.0]

    async def pub(ev):
        pass
    p = Poller({"base": None, "pred": None}, pub, clock=lambda: t[0])
    assert Poller.cell({"live": 0, "ready": 0}) == "cold"
    assert Poller.cell({"live": 1, "ready": 0}) == "starting"
    assert Poller.cell({"live": 0, "ready": 0, "queued": 1}) == "starting"
    assert Poller.cell({"live": 1, "ready": 1}) == "warm"
    p.ingest("base", {"a": {"live": 1, "ready": 0}, "b": {"live": 0, "ready": 0}}, 0.0)
    p.ingest("pred", {"a": {"live": 2, "ready": 0}, "b": {"live": 1, "ready": 1}}, 0.0)
    p.record_sample(0.0)
    p.ingest("base", {"a": {"live": 1, "ready": 1}, "b": {"live": 0, "ready": 0}}, 2.0)
    p.ingest("pred", {"a": {"live": 2, "ready": 2}, "b": {"live": 1, "ready": 1}}, 2.0)
    p.record_sample(2.0)
    assert p.grid["pred"]["b"]["warm_for_s"] == 2.0 and p.grid["base"]["a"]["warm_for_s"] == 0.0
    c = p.contention(0.0, 1.0)
    assert c["max_starting"] == 3 and c["max_starting_by_stack"] == {"base": 1, "pred": 2}
    assert p.contention(5.0, 6.0)["samples"] == 0
    assert not p.all_zero()


# ---------------------------------------------------------------- web app
def test_app_routes_offline() -> None:
    from starlette.testclient import TestClient

    from console.app import Console, make_app

    c = Console(ScriptedStreamingLLM([]), base=FakeStack("base"), pred=FakeStack("pred"),
                start_poller=False)
    with TestClient(make_app(c)) as cl:
        r = cl.get("/")
        assert r.status_code == 200 and "Skypiea Live Console" in r.text
        assert "http" not in r.text.replace("http-equiv", "")   # no external resources
        js = cl.get("/static/app.js")
        assert js.status_code == 200 and js.headers["cache-control"] == "no-cache"
        s = cl.get("/api/state").json()
        assert s["settings"]["hint"] is False and s["settings"]["mirror"] == "concurrent"
        assert s["demo"]["profile"] == "support" and "tau2-bench" in s["demo"]["profile_label"]
        assert cl.post("/api/hint", json={"on": True}).status_code == 202
        assert c.settings["hint"] is True
        assert cl.post("/api/mirror", json={"mode": "bogus"}).status_code == 400
        assert cl.post("/api/policy", json={"key": "always_warm"}).status_code == 400
        assert cl.post("/api/policy", json={"key": "keep_alive_30"}).status_code == 202
        assert c.stacks["base"].policies[-1] == "keep_alive_30"
        assert cl.post("/api/chat", json={"text": "  "}).status_code == 400
        r = cl.get("/api/export.csv")
        assert r.status_code == 200 and r.text.splitlines()[0] == ",".join(CSV_FIELDS)


def test_null_arguments_are_dropped_and_tool_errors_reach_the_llm() -> None:
    from types import SimpleNamespace

    from console.engine import llm_content

    gws = {"base": FakeGW("base"), "pred": FakeGW("pred")}

    async def publish(ev):
        pass
    conv = Conversation(AIRLINE, ScriptedStreamingLLM([[("flight_search", {
        "origin": "GOI", "destination": "HYD", "day": None})]]),
        {"base": FakeStack("base"), "pred": FakeStack("pred")}, NullPoller(), publish,
        {"mirror": "concurrent", "hint": False, "baseline_policy": "scale_to_zero"},
        mcp_factory=factory(gws))
    asyncio.run(conv.user_turn("x"))
    assert gws["pred"].seen[0][2] == {"origin": "GOI", "destination": "HYD", "action": "search"}
    err = {"content": [{"type": "text", "text": "day: Input should be a valid integer"}],
           "isError": True}
    out = llm_content(err, SimpleNamespace(ok=False, error="tool returned isError"))
    assert "valid integer" in out


def test_baseline_lane_sees_the_same_gaps_as_the_predictive_stack() -> None:
    """A slow (cold) baseline must not give the predictive stack extra lead time, and the
    baseline must get the same gap after its own previous call."""
    gws = {"base": FakeGW("base", cold_s=0.4), "pred": FakeGW("pred")}

    async def publish(ev):
        pass
    llm = ScriptedStreamingLLM([[("user_lookup", {}), ("reservation_lookup", {}),
                                 ("flight_search", {})]])
    conv = Conversation(AIRLINE, llm, {"base": FakeStack("base"), "pred": FakeStack("pred")},
                        NullPoller(), publish, {"mirror": "concurrent", "hint": False,
                                                "baseline_policy": "scale_to_zero"},
                        mcp_factory=factory(gws))
    asyncio.run(conv.user_turn("x"))
    assert len(conv.calls) == 3 and [c["seq"] for c in conv.calls] == [0, 1, 2]
    for c in conv.calls[1:]:
        assert c["base_gap_ms"] == pytest.approx(c["pred_gap_ms"], abs=60)
    # the predictive stack did not wait for the slow baseline: its calls are back to back
    p = gws["pred"].times
    assert p[2][0] - p[0][0] < 0.4


def test_sequential_mode_runs_the_baseline_after_the_turn_without_overlap() -> None:
    gws = {"base": FakeGW("base", cold_s=0.1), "pred": FakeGW("pred", cold_s=0.1)}

    async def publish(ev):
        pass
    llm = ScriptedStreamingLLM([[("user_lookup", {}), ("reservation_lookup", {})]])
    conv = Conversation(AIRLINE, llm, {"base": FakeStack("base"), "pred": FakeStack("pred")},
                        NullPoller(), publish, {"mirror": "sequential", "hint": False,
                                                "baseline_policy": "scale_to_zero"},
                        mcp_factory=factory(gws))
    asyncio.run(conv.user_turn("x"))
    last_pred_end = gws["pred"].times[-1][1]
    assert all(t0 >= last_pred_end for t0, _ in gws["base"].times)
    assert conv.calls[1]["base_gap_ms"] == pytest.approx(conv.calls[1]["pred_gap_ms"], abs=60)


def test_records_keep_conversation_order_across_turns() -> None:
    gws = {"base": FakeGW("base", cold_s=0.2), "pred": FakeGW("pred")}

    async def publish(ev):
        pass
    llm = ScriptedStreamingLLM([[("user_lookup", {}), ("reservation_lookup", {}),
                                 ("flight_search", {})], [("reservation_modify", {})]])
    conv = Conversation(AIRLINE, llm, {"base": FakeStack("base"), "pred": FakeStack("pred")},
                        NullPoller(), publish, {"mirror": "concurrent", "hint": False,
                                                "baseline_policy": "scale_to_zero"},
                        mcp_factory=factory(gws))

    async def go():
        await conv.user_turn("a")
        await conv.user_turn("b")
    asyncio.run(go())
    assert [c["seq"] for c in conv.calls] == [0, 1, 2, 3]
    assert [c["tool"] for c in conv.calls] == ["user_lookup", "reservation_lookup",
                                               "flight_search", "reservation_modify"]
    assert sorted(conv.args) == [0, 1, 2, 3]


def test_autopilot_runs_n_turns_and_stays_on_until_done() -> None:
    from console.app import Console

    gws = {"base": FakeGW("base"), "pred": FakeGW("pred")}
    llm = ScriptedStreamingLLM([[("user_lookup", {})], [("reservation_lookup", {})],
                                [("flight_search", {})]])
    c = Console(llm, base=FakeStack("base"), pred=FakeStack("pred"), start_poller=False,
                mcp_factory=factory(gws))
    c.poller = NullPoller()
    c.settings["autopilot_cpm"] = 0          # no typing model in this test
    seen = []

    async def go():
        c.new_conversation()
        t = asyncio.create_task(c.autopilot(turns=1))
        await asyncio.sleep(0)
        seen.append(c.settings["autopilot"])
        await t
    asyncio.run(go())
    assert seen == [True] and c.settings["autopilot"] is False
    assert c.conv.turn == 1 and [x["tool"] for x in c.conv.calls] == ["user_lookup"]


def test_a_slow_predictive_call_does_not_delay_the_baseline() -> None:
    gws = {"base": FakeGW("base"), "pred": FakeGW("pred", cold_s=0.4)}

    async def publish(ev):
        pass
    llm = ScriptedStreamingLLM([[("user_lookup", {}), ("reservation_lookup", {})]])
    conv = Conversation(AIRLINE, llm, {"base": FakeStack("base"), "pred": FakeStack("pred")},
                        NullPoller(), publish, {"mirror": "concurrent", "hint": False,
                                                "baseline_policy": "scale_to_zero"},
                        mcp_factory=factory(gws))
    asyncio.run(conv.user_turn("x"))
    # both stacks started the first call together (no 0.4 s delay for the baseline)
    assert abs(gws["base"].times[0][0] - gws["pred"].times[0][0]) < 0.15
    # the next call only exists once the LLM has the (slower) predictive result, so the
    # baseline's gap is longer by the difference - recorded, never hidden
    c = conv.calls[1]
    excess = c["base_gap_ms"] - c["pred_gap_ms"]
    assert 300 < excess < 520


def _conv(mode, gws=None, script=None):
    gws = gws or {"base": FakeGW("base"), "pred": FakeGW("pred")}
    stacks = {"base": FakeStack("base"), "pred": FakeStack("pred")}

    async def publish(ev):
        pass
    llm = ScriptedStreamingLLM(script or [[("user_lookup", {})], [("reservation_lookup", {})]])
    conv = Conversation(AIRLINE, llm, stacks, NullPoller(), publish,
                        {"mirror": "concurrent", "hint_mode": mode,
                         "baseline_policy": "scale_to_zero"}, mcp_factory=factory(gws))
    return conv, stacks


def test_typing_hint_once_per_turn_plus_open_hint_and_lead_time() -> None:
    conv, stacks = _conv("typing")

    async def go():
        await conv.open_hint()                 # conversation opens: hint for turn 1
        await conv.typing_started()            # first keystroke of turn 1
        await conv.typing_started()            # more keystrokes: no new hint
        await asyncio.sleep(0.2)
        await conv.user_turn("a", "presenter")
        await conv.typing_started()            # turn 2
        await conv.user_turn("b", "presenter")
    asyncio.run(go())
    # open + typing for turn 1, and the Enter re-send of every turn (typing can outlast TTL)
    assert stacks["pred"].hints == [(conv.sid, 1), (conv.sid, 1), (conv.sid, 1),
                                    (conv.sid, 2), (conv.sid, 2)]
    assert stacks["base"].hints == []
    t1, t2 = conv.turns
    assert t1["hint_sources"] == "open+typing+enter" and t1["lead_ms"] >= 200
    assert t1["typing_ms"] >= 200 and t1["first_tool"] == "user_lookup"
    assert t2["hint_sources"] == "typing+enter" and t2["hint_mode"] == "typing"


def test_enter_mode_hints_at_send_and_off_never() -> None:
    conv, stacks = _conv("enter")

    async def go():
        await conv.typing_started()
        await conv.user_turn("a")
    asyncio.run(go())
    assert stacks["pred"].hints == [(conv.sid, 1)]
    assert conv.turns[0]["hint_sources"] == "enter" and conv.turns[0]["lead_ms"] < 50
    conv, stacks = _conv("off")

    async def go2():
        await conv.open_hint()
        await conv.typing_started()
        await conv.user_turn("a")
    asyncio.run(go2())
    assert stacks["pred"].hints == [] and conv.turns[0]["lead_ms"] is None


def test_cancelled_typing_reports_an_abandoned_hint() -> None:
    conv, _ = _conv("typing")

    async def go():
        await conv.typing_started()
        return conv.typing_cancelled()
    pend = asyncio.run(go())
    assert pend["turn"] == 1 and pend["session"] == conv.sid
    conv2, _ = _conv("enter")
    assert conv2.typing_cancelled() is None              # no typing hint, nothing abandoned


def test_turn_records_export_without_text() -> None:
    from console.engine import TURN_FIELDS

    conv, _ = _conv("typing")
    asyncio.run(conv.user_turn("secret customer text", "autopilot", typing_ms=1234.0))
    text = to_csv(conv.turns, TURN_FIELDS)
    assert "secret" not in text and text.splitlines()[0] == ",".join(TURN_FIELDS)
    assert conv.turns[0]["typing_ms"] == 1234.0


def test_app_hint_modes_and_typing_route() -> None:
    from starlette.testclient import TestClient

    from console.app import Console, make_app

    c = Console(ScriptedStreamingLLM([]), base=FakeStack("base"), pred=FakeStack("pred"),
                start_poller=False)
    with TestClient(make_app(c)) as cl:
        assert cl.post("/api/hint", json={"mode": "typing"}).json()["settings"]["hint_mode"] \
            == "typing"
        assert cl.post("/api/hint", json={"mode": "later"}).status_code == 400
        assert cl.post("/api/hint", json={"on": True}).json()["settings"]["hint_mode"] == "enter"
        assert cl.post("/api/typing", json={"event": "start"}).status_code == 202
        assert cl.post("/api/typing", json={"event": "x"}).status_code == 400
        assert cl.post("/api/lookahead", json={"value": 3}).status_code == 400
        assert cl.get("/api/export_turns.csv").status_code == 200
