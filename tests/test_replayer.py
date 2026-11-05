"""Replayer: open loop across sessions, real gaps between batches, concurrent batches, CSV."""

from __future__ import annotations

import pytest  # noqa: E402

pytest.importorskip("httpx", reason="make setup")

import asyncio
import json

import httpx
import pandas as pd
import pytest

from replayer.replay import FIELDS, Config, Replayer, arrivals, load_sessions, write_csv


def trace(tmp_path):
    rows = []
    # s1: batch 0 = two parallel calls (0-100 ms, 0-300 ms), batch 1 starts at 1300 -> gap 1000
    for b, pos, t, svc, tool in [(0, 0, 0, 100, "read_file"), (0, 1, 0, 300, "search"),
                                 (1, 0, 1300, 50, "edit_file"), (2, 0, 1450, 10, "shell")]:
        rows.append(dict(session_id="s1", batch_id=b, position_in_batch=pos, t_offset_ms=t,
                         service_ms=svc, tool=tool, has_timing=True, split="test"))
    for b in range(2):   # untimed session (tau-bench like)
        rows.append(dict(session_id="s2", batch_id=b, position_in_batch=0, t_offset_ms=None,
                         service_ms=None, tool="user_lookup", has_timing=False, split="test"))
    rows.append(dict(session_id="s3", batch_id=0, position_in_batch=0, t_offset_ms=0,
                     service_ms=5, tool="shell", has_timing=True, split="train"))
    p = tmp_path / "calls.parquet"
    pd.DataFrame(rows).to_parquet(p)
    return p


def cfg(path, **kw) -> Config:
    c = Config(sessions_path=str(path), **{"n_sessions": 10, "mode": "functional",
                                           "default_gap_ms": 500, **kw})
    c.validate()
    return c


def test_results_mode_requires_real_gaps(tmp_path):
    with pytest.raises(ValueError):
        Config(sessions_path="x", mode="results", gap_scale=0.2).validate()
    Config(sessions_path="x", mode="results").validate()


def test_sessions_batches_and_gaps(tmp_path):
    ss = {s.source_id: s for s in load_sessions(cfg(trace(tmp_path)))}
    assert set(ss) == {"s1", "s2"}                        # split=test only
    b = ss["s1"].batches
    assert [len(x.calls) for x in b] == [2, 1, 1]
    assert [x.gap_ms for x in b] == [0.0, 1000.0, 100.0]  # start - previous batch end
    assert [x.gap_ms for x in ss["s2"].batches] == [0.0, 500.0]   # default gap, no timing


def test_arrivals_open_loop():
    c = Config(sessions_path="x", arrival={"mode": "fixed", "interval_s": 2})
    assert arrivals(c, 3) == [0.0, 2.0, 4.0]
    p = arrivals(Config(sessions_path="x", arrival={"mode": "poisson", "rate_per_s": 5}), 50)
    assert p == sorted(p) and p[0] == 0 and 5 < p[-1] < 20


def test_replay_concurrency_gaps_headers_and_csv(tmp_path):
    sent = []

    async def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "DELETE":
            return httpx.Response(204)
        body = json.loads(req.content)
        sent.append((asyncio.get_running_loop().time(), body["params"]["name"],
                     body["params"]["arguments"]["service_ms"],
                     req.headers.get("x-skypiea-oracle")))
        await asyncio.sleep(body["params"]["arguments"]["service_ms"] / 1000)
        cold = body["params"]["name"] == "read_file"
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"],
                                         "result": {"content": [], "isError": False}},
                              headers={"X-Skypiea-Cold": "1" if cold else "0",
                                       "X-Skypiea-Held-Ms": "150.0" if cold else "0.0",
                                       "X-Skypiea-Added-Ms": "12.5",
                                       "X-Skypiea-Predicted": "0" if cold else "1"})

    c = cfg(trace(tmp_path), n_sessions=1, gap_scale=0.1, service_scale=0.1,
            oracle_hints=True, split="test", seed=3)
    sessions = [s for s in load_sessions(cfg(trace(tmp_path))) if s.source_id == "s1"]
    rep = Replayer(c, "http://gw", httpx.AsyncClient(transport=httpx.MockTransport(handler)),
                   policy_name="oracle", git_commit="abc")
    rows = asyncio.run(rep.run(sessions))
    t = {name: ts for ts, name, _, _ in sent}
    assert abs(t["read_file"] - t["search"]) < 0.02             # batch members concurrent
    # next batch: after the slower member (30 ms) + gap 1000 ms x 0.1
    assert t["edit_file"] - t["read_file"] >= 0.03 + 0.1 - 0.01
    assert {n: svc for _, n, svc, _ in sent}["search"] == 30     # 300 ms x service_scale 0.1
    assert json.loads(next(o for _, n, _, o in sent if n == "read_file")) == [
        {"tool_class": "edit_file", "in_ms": 100.0}]
    by = {r["tool_class"]: r for r in rows}
    assert by["read_file"]["cold"] and by["read_file"]["hold_ms"] == 150.0
    assert by["search"]["predicted"] and by["search"]["added_latency_ms"] == 12.5
    assert all(r["ok"] and r["policy"] == "oracle" and r["mode"] == "functional" for r in rows)
    out = tmp_path / "out.csv"
    write_csv(rows, out)
    df = pd.read_csv(out)
    assert list(df.columns) == FIELDS and len(df) == 4


def test_replay_records_failures_without_crashing(tmp_path):
    def dead(req):
        raise httpx.ConnectError("refused")

    c = cfg(trace(tmp_path), n_sessions=1, gap_scale=0.01)
    rep = Replayer(c, "http://gw", httpx.AsyncClient(transport=httpx.MockTransport(dead)))
    rows = asyncio.run(rep.run(load_sessions(c)[:1]))
    assert rows and all(not r["ok"] and "ConnectError" in r["error"] for r in rows)
