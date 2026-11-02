"""Gateway: hold (FR-9), cold path, predictor fallback (FR-4), stuck Pending, sessions (NFR-6),
contract validation and the placeholder policies. All timings here are functional only."""

from __future__ import annotations

import pytest  # noqa: E402

pytest.importorskip("httpx", reason="pip install -r requirements-infra.txt")
pytest.importorskip("starlette", reason="pip install -r requirements-infra.txt")
pytest.importorskip("prometheus_client", reason="pip install -r requirements-infra.txt")

import asyncio
import json

import httpx
import pytest

from gateway.app import Gateway, GatewayConfig
from gateway.contract import ContractError, DecideRequest, HistoryItem, OracleHint, parse_response
from gateway.controller_client import RecordingController
from gateway.predictor import FakePredictor, InProcessPredictorClient
from gateway.predictor_stub import StubPredictor
from gateway.readiness import ReadyTracker
from gateway.routing import Router

META = {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {}}
GOOD = {"ranked": [{"tool_class": "policy_check", "p": 0.7}],
        "decisions": [{"tool_class": "policy_check", "warm": True, "copies": 1,
                       "start_in_ms": 0, "ttl_s": None, "prob": 0.7}],
        "model_version": "fake-1", "latency_ms": 1.0}


def call(name: str, i: int = 1, **args) -> dict:
    return {"jsonrpc": "2.0", "id": i, "method": "tools/call",
            "params": {"name": name, "arguments": {"service_ms": 0, **args}, "_meta": META}}


def upstream(seen: list):
    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        seen.append((str(req.url), body, dict(req.headers)))
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {
            "content": [{"type": "text", "text": "{}"}], "isError": False}})
    return handler


def make(predictor=None, ready: dict | None = None, ready_after: float | None = None,
         **cfg) -> tuple[Gateway, RecordingController, ReadyTracker, list]:
    tracker = ReadyTracker()
    for s, n in (ready or {}).items():
        tracker.set_ready(s, n)

    async def on_warm(cls, copies):  # the cluster brings a pod up after `ready_after` s
        if ready_after is not None:
            server = router.resolve(cls).server
            asyncio.get_running_loop().call_later(ready_after, tracker.set_ready, server, 1)

    router = Router("ns", url_template="http://{svc}.{ns}/mcp")
    ctrl = RecordingController(on_warm=on_warm)
    seen: list = []
    http = httpx.AsyncClient(transport=httpx.MockTransport(upstream(seen)))
    gw = Gateway(GatewayConfig(**{"hold_ms": 150, "cold_timeout_s": 2, **cfg}), router,
                 predictor, ctrl, tracker, http)
    return gw, ctrl, tracker, seen


async def post(gw: Gateway, body: dict, sid: str | None = "s1", headers: dict | None = None):
    h = {"content-type": "application/json", **({"Mcp-Session-Id": sid} if sid else {}),
         **(headers or {})}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(gw.app(background=False)),
                                 base_url="http://gw") as c:
        r = await c.post("/mcp", json=body, headers=h)
    await gw.drain()
    return r


def counter(m, name: str, **labels) -> float:
    return m.registry.get_sample_value(name, labels) or 0.0


# ---------------- routing ----------------

def test_routing_classes_servers_and_unknown_names():
    r = Router("skypiea-tools")
    cases = {"write_file": ("write_file", "edit_file"), "edit_file": ("write_file", "edit_file"),
             "order_return": ("order_return", "refund_issue"),
             "other_support": ("other_support", "generic_fallback"),
             "mcp__weird": ("other", "generic_fallback")}
    for name, want in cases.items():
        got = r.resolve(name)
        assert (got.tool_class, got.server) == want, name
    assert r.resolve("run_tests").url == \
        "http://run-tests.skypiea-tools.svc.cluster.local:80/mcp"


# ---------------- warm / hold / cold ----------------

def test_warm_pod_forwards_immediately_and_rewrites_name():
    async def go():
        gw, ctrl, _, seen = make(ready={"edit_file": 1})
        r = await post(gw, call("write_file", path="a.py"))
        assert r.status_code == 200 and r.headers["X-Skypiea-Cold"] == "0"
        assert r.headers["Mcp-Session-Id"] == "s1"
        url, body, headers = seen[0]
        assert url == "http://edit-file.ns/mcp" and body["params"]["name"] == "edit_file"
        assert headers["mcp-name"] == "edit_file" and headers["mcp-method"] == "tools/call"
        assert body["params"]["arguments"]["path"] == "a.py" and body["params"]["_meta"] == META
        assert ctrl.warms == []                                    # no demand needed
        assert counter(gw.metrics, "skypiea_gateway_prewarm_hits_total",
                       tool="write_file") == 1
    asyncio.run(go())


def test_hold_catches_a_pod_that_becomes_ready_within_150ms():
    async def go():
        gw, ctrl, _, _ = make(ready_after=0.05)
        r = await post(gw, call("order_lookup"))
        assert r.headers["X-Skypiea-Cold"] == "0"                  # served after a short hold
        assert 30 <= float(r.headers["X-Skypiea-Held-Ms"]) <= 150
        assert ctrl.warms[0] == ("order_lookup", 1, 0, "demand")
        assert counter(gw.metrics, "skypiea_gateway_calls_total", tool="order_lookup",
                       cold="false") == 1
    asyncio.run(go())


def test_after_150ms_hold_the_call_goes_cold_and_still_succeeds():
    async def go():
        gw, _, _, seen = make(ready_after=0.4)
        r = await post(gw, call("run_tests"))
        assert r.headers["X-Skypiea-Cold"] == "1" and "result" in r.json()
        assert len(seen) == 1
        assert counter(gw.metrics, "skypiea_gateway_calls_total", tool="run_tests",
                       cold="true") == 1
        assert counter(gw.metrics, "skypiea_gateway_hold_ms_count", tool="run_tests") == 1
    asyncio.run(go())


def test_pod_stuck_pending_fails_the_call_cleanly_after_cold_timeout():
    async def go():
        gw, _, _, seen = make(ready_after=None, cold_timeout_s=0.3)   # never Ready
        r = await post(gw, call("policy_check"))
        err = r.json()["error"]
        assert err["code"] == -32001 and "not ready" in err["message"] and seen == []
        assert counter(gw.metrics, "skypiea_gateway_not_ready_total", tool="policy_check") == 1
        assert gw.inflight["policy_check"] == 0 and gw.sessions.get("s1").inflight == 0
    asyncio.run(go())


def test_controller_down_does_not_fail_warm_calls():
    async def go():
        gw, ctrl, _, _ = make(ready={"shell": 1})
        ctrl.fail = RuntimeError("controller down")
        r = await post(gw, call("shell"))
        assert "result" in r.json()
    asyncio.run(go())


# ---------------- predictor: off the critical path, FR-4 fallback ----------------

def test_predictor_decision_reaches_controller_with_default_ttl():
    async def go():
        fake = FakePredictor(GOOD)
        gw, ctrl, _, _ = make(predictor=fake, ready={"order_lookup": 1})
        await post(gw, call("order_lookup"))
        req = fake.requests[0]
        assert req.session_id == "s1" and [h.tool_class for h in req.history] == ["order_lookup"]
        assert ("policy_check", 1, None, "predict:fake-1") in ctrl.warms
    asyncio.run(go())


def test_start_in_ms_delays_the_warm():
    async def go():
        ans = {**GOOD, "decisions": [{**GOOD["decisions"][0], "start_in_ms": 200}]}
        gw, ctrl, _, _ = make(predictor=FakePredictor(ans), ready={"order_lookup": 1})
        t0 = asyncio.get_running_loop().time()
        await post(gw, call("order_lookup"))
        assert asyncio.get_running_loop().time() - t0 >= 0.2 and len(ctrl.warms) == 1
    asyncio.run(go())


@pytest.mark.parametrize("kind", ["dead", "slow", "garbage", "sum_above_1", "nan", "not_json"])
def test_predictor_failures_fall_back_to_scale_to_zero(kind):
    """FR-4: never fail a call because of the predictor; no warm decision is applied."""
    async def go():
        answers = {
            "dead": FakePredictor(fail=httpx.ConnectError("refused")),
            "slow": FakePredictor(GOOD, delay_s=1.0),
            "garbage": FakePredictor({"ranked": "x", "decisions": None}),
            "sum_above_1": FakePredictor({**GOOD, "ranked": [{"tool_class": "a", "p": 0.7},
                                                             {"tool_class": "b", "p": 0.6}]}),
            "nan": FakePredictor({**GOOD, "decisions": [{**GOOD["decisions"][0],
                                                         "prob": float("nan")}]}),
            "not_json": FakePredictor(fail=json.JSONDecodeError("x", "y", 0)),
        }
        gw, ctrl, _, _ = make(predictor=answers[kind], ready={"order_lookup": 1},
                              predictor_timeout_s=0.1)
        r = await post(gw, call("order_lookup"))
        assert "result" in r.json() and r.headers["X-Skypiea-Cold"] == "0"
        assert ctrl.warms == []
        errs = sum(s.value for m in gw.metrics.registry.collect() for s in m.samples
                   if s.name == "skypiea_gateway_predictor_errors_total")
        assert errs == 1
    asyncio.run(go())


def test_predictor_is_not_on_the_critical_path():
    async def go():
        gw, _, _, _ = make(predictor=FakePredictor(GOOD, delay_s=0.25), ready={"shell": 1},
                           predictor_timeout_s=1.0)
        app = gw.app(background=False)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://g") as c:
            t0 = asyncio.get_running_loop().time()
            await c.post("/mcp", json=call("shell"), headers={"Mcp-Session-Id": "s"})
            assert asyncio.get_running_loop().time() - t0 < 0.2   # reply before the predictor
        await gw.drain()
    asyncio.run(go())


# ---------------- sessions ----------------

def test_sessions_isolated_created_and_purged():
    async def go():
        fake = FakePredictor()
        gw, _, _, _ = make(predictor=fake, ready={"read_file": 1, "run_tests": 1},
                           session_idle_s=600)
        r = await post(gw, call("read_file"), sid=None)
        new_sid = r.headers["Mcp-Session-Id"]
        assert len(new_sid) == 32
        await post(gw, call("run_tests"), sid="other")
        await post(gw, call("run_tests"), sid=new_sid)
        hist = {q.session_id: [h.tool_class for h in q.history] for q in fake.requests}
        assert hist == {new_sid: ["read_file", "run_tests"], "other": ["run_tests"]}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(gw.app(background=False)),
                                     base_url="http://g") as c:
            assert (await c.delete("/mcp", headers={"Mcp-Session-Id": new_sid})).status_code == 204
        assert gw.sessions.purge() == 1 and new_sid not in gw.sessions and "other" in gw.sessions
    asyncio.run(go())


def test_idle_sessions_purged_after_idle_timeout():
    t = [0.0]
    gw, _, _, _ = make(session_idle_s=60)
    gw.sessions.clock = lambda: t[0]
    gw.sessions.get("a")
    t[0] = 59
    assert gw.sessions.purge() == 0
    t[0] = 61
    assert gw.sessions.purge() == 1


def test_batch_ids_group_overlapping_calls():
    async def go():
        fake = FakePredictor()
        gw, _, tracker, _ = make(predictor=fake, ready_after=0.05)
        tracker.set_ready("search", 0)
        app = gw.app(background=False)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://g") as c:
            h = {"Mcp-Session-Id": "b"}
            await asyncio.gather(c.post("/mcp", json=call("search", 1), headers=h),
                                 c.post("/mcp", json=call("read_file", 2), headers=h))
            await c.post("/mcp", json=call("shell", 3), headers=h)
        await gw.drain()
        last = fake.requests[-1].history
        assert [h.batch_id for h in last] == [0, 0, 1]
    asyncio.run(go())


def test_protocol_surface():
    async def go():
        gw, _, _, _ = make()
        r = await post(gw, {"jsonrpc": "2.0", "id": 7, "method": "tools/list", "params": {}})
        names = {t["name"] for t in r.json()["result"]["tools"]}
        assert {"order_lookup", "write_file", "edit_file", "generic_fallback"} <= names
        r = await post(gw, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        assert r.status_code == 202
        r = await post(gw, {"jsonrpc": "2.0", "id": 8, "method": "resources/list"})
        assert r.json()["error"]["code"] == -32601
    asyncio.run(go())


# ---------------- contract ----------------

def test_contract_accepts_good_and_rejects_bad():
    assert parse_response(GOOD).decisions[0].ttl_s is None
    bad = [None, [], {**GOOD, "model_version": ""},
           {**GOOD, "decisions": [{**GOOD["decisions"][0], "copies": 1.5}]},
           {**GOOD, "decisions": [{**GOOD["decisions"][0], "warm": "yes"}]},
           {**GOOD, "decisions": [{**GOOD["decisions"][0], "ttl_s": -1}]},
           {**GOOD, "decisions": [{**GOOD["decisions"][0], "copies": 99}]},
           {**GOOD, "ranked": [{"tool_class": "a", "p": 1.2}]}]
    for b in bad:
        with pytest.raises(ContractError):
            parse_response(b)
    # decisions may sum above 1 (multi-branch); ranked may not (FR-1)
    two = [{**GOOD["decisions"][0], "tool_class": t, "prob": 0.8} for t in ("a", "b")]
    assert len(parse_response({**GOOD, "decisions": two}).decisions) == 2


def test_request_round_trip():
    req = DecideRequest("s", 12.5, [HistoryItem("read_file", 0, 5, False, 0)],
                        [OracleHint("run_tests", 300)])
    assert DecideRequest.from_json(json.loads(json.dumps(req.to_json()))) == req


# ---------------- placeholder policies (functional stand-ins) ----------------

def _req(seq: list[str], sid: str = "s", oracle=()) -> DecideRequest:
    return DecideRequest(sid, 0, [HistoryItem(c, 0, 1, False, i) for i, c in enumerate(seq)],
                         list(oracle))


def test_stub_policies_follow_the_contract():
    stub = StubPredictor()
    for name in ["scale_to_zero", "always_warm", "keep_alive", "markov1", "predictive",
                 "oracle"]:
        stub.set_policy(name, {})
        out = parse_response(stub.decide(_req(["order_lookup"],
                                              oracle=[OracleHint("policy_check", 5000)])))
        assert out.model_version == f"stub-{name}"
    stub.set_policy("scale_to_zero", {})
    assert stub.decide(_req(["order_lookup"]))["decisions"] == []
    stub.set_policy("keep_alive", {"ttl_s": 30})
    d = stub.decide(_req(["order_lookup"]))["decisions"]
    assert [(x["tool_class"], x["ttl_s"]) for x in d] == [("order_lookup", 30)]
    stub.set_policy("oracle", {})
    d = stub.decide(_req(["order_lookup"], oracle=[OracleHint("policy_check", 5000)]))
    assert d["decisions"][0]["tool_class"] == "policy_check"
    assert 0 <= d["decisions"][0]["start_in_ms"] < 5000   # starts early by the cold start


def test_markov1_learns_online_and_warms_top1_above_tau():
    stub = StubPredictor("markov1", {"tau": 0.6})
    assert stub.decide(_req(["read_file"], "t0"))["decisions"] == []
    for i in range(3):
        stub.decide(_req(["read_file", "run_tests"], f"t{i}"))
    stub.decide(_req(["read_file", "format_diff"], "t9"))
    d = stub.decide(_req(["read_file"], "new"))
    assert [x["tool_class"] for x in d["decisions"]] == ["run_tests"]   # p = 0.75 >= 0.6
    assert sum(r["p"] for r in d["ranked"]) <= 1.0 + 1e-9


def test_in_process_client_wraps_stub():
    async def go():
        stub = StubPredictor("keep_alive", {"ttl_s": 7})
        gw, ctrl, _, _ = make(predictor=InProcessPredictorClient(stub.decide),
                              ready={"read_file": 1})
        await post(gw, call("read_file"))
        assert ctrl.warms == [("read_file", 1, 7, "predict:stub-keep_alive")]
    asyncio.run(go())


def test_forwards_to_ready_pod_address_not_the_service():
    async def go():
        gw, _, tracker, seen = make()
        tracker.set_ready("run_tests", 2, ["10.0.0.5:8080", "10.0.0.6:8080"])
        await post(gw, call("run_tests", 1))
        await post(gw, call("run_tests", 2))
        assert {u for u, _, _ in seen} == {"http://10.0.0.5:8080/mcp", "http://10.0.0.6:8080/mcp"}
    asyncio.run(go())


def test_refused_connections_are_retried_until_routable():
    """Kind run: the pod was Ready but the Service did not route yet -> connection refused.
    The gateway must retry with backoff instead of failing the call."""
    async def go():
        attempts = []

        def flaky(req: httpx.Request) -> httpx.Response:
            attempts.append(1)
            if len(attempts) < 3:
                raise httpx.ConnectError("connection refused")
            body = json.loads(req.content)
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {
                "content": [], "isError": False}})

        gw, _, _, _ = make(ready={"shell": 1})
        gw.http = httpx.AsyncClient(transport=httpx.MockTransport(flaky))
        r = await post(gw, call("shell"))
        assert "result" in r.json() and len(attempts) == 3
        assert counter(gw.metrics, "skypiea_gateway_connect_retries_total", tool="shell") == 2
    asyncio.run(go())


def test_connect_failures_give_up_after_connect_retry_s():
    async def go():
        def dead(req: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        gw, _, _, _ = make(ready={"shell": 1}, connect_retry_s=0.2)
        gw.http = httpx.AsyncClient(transport=httpx.MockTransport(dead))
        r = await post(gw, call("shell"))
        assert r.json()["error"]["code"] == -32002
        assert gw.inflight["shell"] == 0
    asyncio.run(go())


def test_session_introspection_predicted_flag_and_decision_log():
    async def go():
        gw, _, _, _ = make(predictor=FakePredictor(GOOD), ready={"order_lookup": 1,
                                                                  "policy_check": 1})
        r1 = await post(gw, call("order_lookup"), sid="x")
        assert r1.headers["X-Skypiea-Predicted"] == "0"          # nothing predicted yet
        r2 = await post(gw, call("policy_check", 2), sid="x")
        assert r2.headers["X-Skypiea-Predicted"] == "1"          # previous decision warmed it
        assert float(r2.headers["X-Skypiea-Added-Ms"]) >= 0
        async with httpx.AsyncClient(transport=httpx.ASGITransport(gw.app(background=False)),
                                     base_url="http://g") as c:
            s = (await c.get("/v1/sessions/x")).json()
            assert [x["tool_class"] for x in s["calls"]] == ["order_lookup", "policy_check"]
            assert [x["predicted"] for x in s["calls"]] == [False, True]
            d = s["decisions"][0]
            assert d["outcome"] == "ok" and d["model_version"] == "fake-1"
            assert d["decisions"][0]["tool_class"] == "policy_check"
            gw.predictor = FakePredictor(fail=httpx.ConnectError("down"))
            await post(gw, call("order_lookup", 3), sid="x")
            s = (await c.get("/v1/sessions/x")).json()
            assert s["decisions"][-1]["outcome"] == "error:ConnectError"
            assert (await c.get("/v1/sessions")).json()["sessions"][0]["calls"] == 3
            await c.delete("/mcp", headers={"Mcp-Session-Id": "x"})
            gw.sessions.purge()
            assert (await c.get("/v1/sessions/x")).status_code == 404
    asyncio.run(go())
