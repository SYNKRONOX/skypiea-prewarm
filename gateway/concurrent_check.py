"""Concurrent-session FUNCTIONAL check (AWS, in-cluster Job): N Scenario A/B sessions at once
through the gateway, per policy. Checks: every call succeeds, the controller never has more
than max_scaleups_per_node x nodes pods starting (polled every 0.5 s from /v1/state), queued
scale-ups are counted, every tool is back at 0 afterwards. No latency printed is a result.
NFR-4 (500 sessions) is covered by the simulator, not by this 2-vCPU node.

    python -m gateway.concurrent_check --export-sessions s.json --n 40     # laptop (pandas)
    python concurrent_check.py --sessions-json s.json --policies scale_to_zero,markov1
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path

import httpx

META = {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {}}


def export(n: int, out: str, sessions_dir: str) -> None:
    import pandas as pd

    per = {"A": n // 2, "B": n - n // 2}
    res = {}
    for label, src in (("A", "synthetic_branching"), ("B", "synthetic_deterministic")):
        d = pd.read_parquet(Path(sessions_dir) / src / "calls.parquet",
                            columns=["session_id", "tool", "t_offset_ms", "service_ms"])
        sess = []
        for _, g in d.groupby("session_id", sort=True):
            g = g.sort_values("t_offset_ms")
            sess.append([{"tool_class": r.tool, "t_ms": float(r.t_offset_ms),
                          "service_ms": float(r.service_ms)} for r in g.itertuples()])
            if len(sess) == per[label]:
                break
        res[label] = sess
    Path(out).write_text(json.dumps(res))
    print(f"wrote {out}: A {len(res['A'])} sessions, B {len(res['B'])} sessions, "
          f"{sum(len(s) for v in res.values() for s in v)} calls")


def metric(text: str, name: str) -> float:
    return sum(float(ln.rsplit(" ", 1)[1]) for ln in text.splitlines()
               if ln.startswith(name + "{") or ln.startswith(name + " "))


async def session(c: httpx.AsyncClient, gw: str, calls: list[dict], gap_scale: float,
                  out: list) -> None:
    sid, prev_end = uuid.uuid4().hex, None
    for i, call in enumerate(calls):
        gap = 0.0 if prev_end is None else max(0.0, call["t_ms"] - prev_end) * gap_scale
        prev_end = call["t_ms"] + call["service_ms"]
        await asyncio.sleep(gap / 1000)
        hdr = {"Mcp-Session-Id": sid, "MCP-Protocol-Version": "2026-07-28",
               "Mcp-Method": "tools/call", "Mcp-Name": call["tool_class"],
               "accept": "application/json, text/event-stream"}
        body = {"jsonrpc": "2.0", "id": i + 1, "method": "tools/call",
                "params": {"name": call["tool_class"],
                           "arguments": {"service_ms": int(call["service_ms"])}, "_meta": META}}
        try:
            r = await c.post(f"{gw}/mcp", json=body, headers=hdr)
            j = r.json()
            ok = "result" in j and not j["result"].get("isError")
            out.append({"ok": ok, "cold": r.headers.get("X-Skypiea-Cold") == "1",
                        "err": None if ok else str(j)[:200]})
        except Exception as e:  # recorded, never dropped
            out.append({"ok": False, "cold": None, "err": f"{type(e).__name__}: {e}"[:200]})
    await c.delete(f"{gw}/mcp", headers={"Mcp-Session-Id": sid})


async def watch(c: httpx.AsyncClient, ctl: str, stop: asyncio.Event, seen: dict) -> None:
    while not stop.is_set():
        try:
            st = (await c.get(f"{ctl}/v1/state", timeout=5)).json()
            starting = sum(max(0, v["live"] - v["ready"]) for v in st.values())
            seen["max_starting"] = max(seen["max_starting"], starting)
            seen["max_queued"] = max(seen["max_queued"],
                                     sum(v.get("queued", 0) for v in st.values()))
            seen["polls"] += 1
        except Exception as e:
            seen["poll_errors"] += 1
            seen["last_poll_error"] = str(e)[:120]
        await asyncio.sleep(0.5)


async def wait_zero(c: httpx.AsyncClient, ctl: str, timeout_s: float) -> float:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout_s:
        st = (await c.get(f"{ctl}/v1/state", timeout=5)).json()
        if all(v["live"] == 0 and v["ready"] == 0 for v in st.values()):
            return time.monotonic() - t0
        await asyncio.sleep(1)
    raise SystemExit(f"tools not back at 0 after {timeout_s}s")


async def run(a) -> int:
    ns = a.namespace
    gw, ctl = f"http://skypiea-gateway.{ns}.svc:80", f"http://skypiea-controller.{ns}.svc:8081"
    pred = f"http://skypiea-predictor.{ns}.svc:80"
    sess = json.loads(Path(a.sessions_json).read_text())
    all_sessions = sess["A"] + sess["B"]
    limit = a.max_starting
    failures = []
    print(f"FUNCTIONAL CHECK ONLY: {len(all_sessions)} concurrent sessions per policy, "
          f"gap x{a.gap_scale}; latencies are not results.\n")
    async with httpx.AsyncClient(timeout=300, limits=httpx.Limits(max_connections=200)) as c:
        for pol in a.policies.split(","):
            await wait_zero(c, ctl, 300)
            if pol != "predictive":   # predictive = the real predictor (gateway serves the model)
                (await c.post(f"{pred}/admin/policy", json={"name": pol, "params": {}})
                 ).raise_for_status()
            m0 = (await c.get(f"{ctl}/metrics")).text
            g0 = (await c.get(f"{gw}/metrics")).text
            stop, seen = asyncio.Event(), {"max_starting": 0, "max_queued": 0, "polls": 0,
                                           "poll_errors": 0}
            w = asyncio.create_task(watch(c, ctl, stop, seen))
            rows: list = []
            t0 = time.monotonic()
            await asyncio.gather(*(session(c, gw, s, a.gap_scale, rows) for s in all_sessions))
            dur = time.monotonic() - t0
            stop.set()
            await w
            m1 = (await c.get(f"{ctl}/metrics")).text
            g1 = (await c.get(f"{gw}/metrics")).text
            d = {k: metric(m1, k) - metric(m0, k) for k in (
                "skypiea_scaleups_queued_total", "skypiea_scaleups_expired_in_queue_total",
                "skypiea_scaleup_queue_wait_seconds_count", "skypiea_scale_refused_total",
                "skypiea_scale_errors_total")}
            gd = {k: metric(g1, k) - metric(g0, k) for k in (
                "skypiea_gateway_predictor_errors_total", "skypiea_gateway_prewarm_hits_total",
                "skypiea_gateway_prewarm_misses_total", "skypiea_gateway_not_ready_total",
                "skypiea_gateway_predictor_latency_seconds_count")}
            n_ok = sum(r["ok"] for r in rows)
            n_cold = sum(1 for r in rows if r["cold"])
            back = await wait_zero(c, ctl, 300)
            print(f"== {pol}: {len(rows)} calls, ok {n_ok}, cold {n_cold}, wall {dur:.0f}s; "
                  f"max starting seen {seen['max_starting']} (limit {limit}), max queued "
                  f"{seen['max_queued']}, polls {seen['polls']} (errors {seen['poll_errors']})")
            print(f"   controller: queued {d['skypiea_scaleups_queued_total']:.0f}, "
                  f"dequeued {d['skypiea_scaleup_queue_wait_seconds_count']:.0f}, expired in "
                  f"queue {d['skypiea_scaleups_expired_in_queue_total']:.0f}, refused by caps "
                  f"{d['skypiea_scale_refused_total']:.0f}, scale errors "
                  f"{d['skypiea_scale_errors_total']:.0f}; all tools at 0 after {back:.0f}s")
            print(f"   gateway: predictor calls "
                  f"{gd['skypiea_gateway_predictor_latency_seconds_count']:.0f}, predictor errors "
                  f"{gd['skypiea_gateway_predictor_errors_total']:.0f}, prewarm hits "
                  f"{gd['skypiea_gateway_prewarm_hits_total']:.0f}, misses "
                  f"{gd['skypiea_gateway_prewarm_misses_total']:.0f}, not ready "
                  f"{gd['skypiea_gateway_not_ready_total']:.0f}")
            for e in [r["err"] for r in rows if not r["ok"]][:5]:
                print(f"   error: {e}")
            checks = {"all calls succeeded": n_ok == len(rows),
                      f"never more than {limit} starting": seen["max_starting"] <= limit,
                      "state polled": seen["polls"] > 10 and seen["poll_errors"] == 0}
            for k, v in checks.items():
                print(f"   check: {k}: {'PASS' if v else 'FAIL'}")
                if not v:
                    failures.append(f"{pol}: {k}")
        if "predictive" not in a.policies.split(","):
            await c.post(f"{pred}/admin/policy", json={"name": "scale_to_zero", "params": {}})
    print("\nFAILURES: " + ("; ".join(failures) if failures else "none"))
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--namespace", default="skypiea-tools")
    ap.add_argument("--sessions-json")
    ap.add_argument("--policies", default="scale_to_zero,markov1")
    ap.add_argument("--gap-scale", type=float, default=0.2)
    ap.add_argument("--max-starting", type=int, default=2)
    ap.add_argument("--export-sessions", metavar="FILE")
    ap.add_argument("--from-test", choices=["A", "B"],
                    help="export N test sessions of that scenario's source (scenario_sessions "
                         "draw) instead of the PRD synthetic ones; ids -> --record")
    ap.add_argument("--record", help="where to write the drawn ids (with --from-test)")
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--sessions-dir", default=os.path.expanduser(
        "~/skypiea-model/data/processed"))
    a = ap.parse_args()
    if a.export_sessions and a.from_test:
        from gateway.scenario_sessions import draw

        calls, rec = draw(Path(a.sessions_dir), a.n, 20261105, 600.0, 0)
        sess = list(calls[a.from_test]["sessions"].values())
        Path(a.export_sessions).write_text(json.dumps({"A": [], "B": sess}))
        r = rec["scenarios"][a.from_test]
        r.pop("markov1_warmup", None)
        Path(a.record).write_text(json.dumps({"seed": 20261105, "n": a.n, a.from_test: r},
                                             indent=1) + "\n")
        print(f"wrote {a.export_sessions}: {len(sess)} {r['source']} test sessions, "
              f"{sum(len(x) for x in sess)} calls")
        return 0
    if a.export_sessions:
        export(a.n, a.export_sessions, a.sessions_dir)
        return 0
    return asyncio.run(run(a))


if __name__ == "__main__":
    sys.exit(main())
