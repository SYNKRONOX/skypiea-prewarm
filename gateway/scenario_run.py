"""Real-timing Scenario runs: replay drawn sessions through the gateway at their REAL think gaps
(unscaled), N sessions per policy with C running at once, and record every call.

Runs INSIDE the cluster as a Job (infra/aws/scripts/replay-job.sh, CHECK=scenario); on kind it
can run from the laptop with port-forwards (functional only there):

    python scenario_run.py --in-cluster --sessions-json sessions.json --scenario A \
        --policies scale_to_zero,always_warm,keep_alive,markov1 [--concurrency 5]
    python scenario_run.py --in-cluster ... --scenario A --policies predictive --profile support

Sessions come from gateway/scenario_sessions.py (recorded in results/scenario_sessions.json).
A session is replayed batch by batch: calls of one batch are sent together; the gap before a
batch is (its first call's offset - end of the previous batch), as recorded.

Policies: scale_to_zero, always_warm, keep_alive (30 s), markov1 = the stub's stand-ins (gateway
must serve the stub); predictive = the real predictor (gateway must serve the model; checked:
its /healthz profile must equal --profile and its request count must grow). Nothing is tuned:
always_warm keeps one pod per server with a 90 s TTL refreshed on every call (as in the
functional checks); markov1 learns from the scenario's TRAIN-split histories first (sent to the
stub's /decide; never test sessions), then online.

Per call (stdout, one `ROW {json}` line each): policy, scenario, session, batch, tool_class,
gap_ms, ok, cold, added_ms, held_ms, predicted (gateway headers). Per policy (`POLICY {json}`):
controller/gateway metric deltas from just before the policy's set-up until every tool is back
at 0 replicas (so idle pod-seconds include the TTL tail): skypiea_pod_seconds_idle_total, scale
events, scale-up queue counters, prewarm hits/misses, predictor errors.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import time
import uuid
from pathlib import Path

import httpx

META = {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {}}
STUB_POLICIES = ["scale_to_zero", "always_warm", "keep_alive", "markov1"]
CTL_METRICS = ["skypiea_pod_seconds_idle_total", "skypiea_scale_events_total",
               "skypiea_scaleups_queued_total", "skypiea_scaleups_expired_in_queue_total",
               "skypiea_scale_refused_total", "skypiea_scale_errors_total",
               "skypiea_warm_requests_total"]
GW_METRICS = ["skypiea_gateway_prewarm_hits_total", "skypiea_gateway_prewarm_misses_total",
              "skypiea_gateway_predictor_errors_total", "skypiea_gateway_not_ready_total",
              "skypiea_gateway_predictor_latency_seconds_count"]


def batches(calls: list[dict]) -> list[tuple[float, list[dict]]]:
    """[(gap_ms before the batch, calls)] in recorded order."""
    out: list[tuple[float, list[dict]]] = []
    groups: dict[int, list[dict]] = {}
    order: list[int] = []
    for c in calls:
        if c["batch_id"] not in groups:
            order.append(c["batch_id"])
            groups[c["batch_id"]] = []
        groups[c["batch_id"]].append(c)
    prev_end = None
    for b in order:
        g = groups[b]
        start = min(c["t_ms"] for c in g)
        gap = 0.0 if prev_end is None else max(0.0, start - prev_end)
        out.append((gap, g))
        prev_end = max(c["t_ms"] + c["service_ms"] for c in g)
    return out


def history(calls: list[dict]) -> list[dict]:
    return [{"tool_class": c["tool_class"], "t_start_ms": c["t_ms"],
             "t_end_ms": c["t_ms"] + c["service_ms"], "error": False,
             "batch_id": c["batch_id"]} for c in calls]


def metrics(text: str, names: list[str]) -> dict[str, float]:
    out = dict.fromkeys(names, 0.0)
    for ln in text.splitlines():
        if ln.startswith("#"):
            continue
        key = ln.split("{", 1)[0].split(" ", 1)[0]
        if key in out:
            out[key] += float(ln.rsplit(" ", 1)[1])
    return out


def nearest_rank(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    k = max(1, math.ceil(q / 100 * len(s)))
    return s[min(k, len(s)) - 1]


class Runner:
    def __init__(self, gw: str, ctl: str, stub: str, model: str, scenario: str, conc: int,
                 started: float):
        self.gw, self.ctl, self.stub, self.model = gw, ctl, stub, model
        self.scenario, self.conc, self.t0 = scenario, conc, started
        self.dataset = "real"

    async def snapshot(self, c: httpx.AsyncClient) -> dict[str, float]:
        m = metrics((await c.get(f"{self.ctl}/metrics")).text, CTL_METRICS)
        m.update(metrics((await c.get(f"{self.gw}/metrics")).text, GW_METRICS))
        return m

    async def wait_zero(self, c: httpx.AsyncClient, timeout_s: float) -> float:
        t = time.monotonic()
        while time.monotonic() - t < timeout_s:
            st = (await c.get(f"{self.ctl}/v1/state")).json()
            if all(v["live"] == 0 and v["ready"] == 0 for v in st.values()):
                return time.monotonic() - t
            await asyncio.sleep(1)
        raise RuntimeError(f"tools not at 0 after {timeout_s}s: "
                           f"{ {k: v['live'] for k, v in st.items() if v['live']} }")

    async def session(self, c: httpx.AsyncClient, pol: str, sid: str, calls: list[dict],
                      sem: asyncio.Semaphore, rows: list[dict]) -> None:
        async with sem:
            msid = uuid.uuid4().hex
            n = 0
            t_sess = time.monotonic()
            for b, (gap, group) in enumerate(batches(calls)):
                await asyncio.sleep(gap / 1000)

                async def one(call: dict, i: int, b: int = b, gap: float = gap) -> dict:
                    hdr = {"Mcp-Session-Id": msid, "MCP-Protocol-Version": "2026-07-28",
                           "Mcp-Method": "tools/call", "Mcp-Name": call["tool_class"],
                           "accept": "application/json, text/event-stream"}
                    body = {"jsonrpc": "2.0", "id": i, "method": "tools/call",
                            "params": {"name": call["tool_class"], "_meta": META,
                                       "arguments": {"service_ms": int(call["service_ms"])}}}
                    t = time.monotonic()
                    row = {"dataset": self.dataset, "policy": pol, "scenario": self.scenario,
                           "session": sid, "batch": b,
                           "tool_class": call["tool_class"], "gap_ms": round(gap, 1),
                           "service_ms": round(call["service_ms"], 1),
                           "t_s": round(t - self.t0, 3)}
                    try:
                        r = await c.post(f"{self.gw}/mcp", json=body, headers=hdr, timeout=300)
                        res = r.json()
                        h = r.headers
                        row.update(ok="result" in res and not res["result"].get("isError"),
                                   cold=h.get("X-Skypiea-Cold") == "1",
                                   added_ms=float(h.get("X-Skypiea-Added-Ms", "nan")),
                                   held_ms=float(h.get("X-Skypiea-Held-Ms", "nan")),
                                   predicted=h.get("X-Skypiea-Predicted") == "1",
                                   error=None if "result" in res else str(res.get("error"))[:200])
                    except (httpx.HTTPError, ValueError) as e:
                        row.update(ok=False, cold=None, added_ms=None, held_ms=None,
                                   predicted=None, error=f"{type(e).__name__}: {e}"[:200])
                    row["wall_ms"] = round((time.monotonic() - t) * 1000, 1)
                    return row

                got = await asyncio.gather(*[one(call, n + k + 1)
                                             for k, call in enumerate(group)])
                n += len(group)
                for row in got:
                    print("ROW " + json.dumps(row), flush=True)
                rows.extend(got)
            await c.delete(f"{self.gw}/mcp", headers={"Mcp-Session-Id": msid})
            print(f"   session {sid[:12]} done: {n} calls, {time.monotonic() - t_sess:.0f}s",
                  flush=True)

    async def setup(self, c: httpx.AsyncClient, pol: str, train: list[list[dict]],
                    profile: str | None) -> dict:
        info: dict = {}
        if pol == "predictive":
            h = (await c.get(f"{self.model}/healthz")).json()
            if h.get("profile") != profile or h.get("status") != "ok":
                raise RuntimeError(f"model /healthz {h} != profile {profile}")
            info["model"] = {k: h.get(k) for k in ("model_version", "profile",
                                                    "cold_start_matches_bundle")}
            return info
        params = {"ttl_s": 90} if pol == "always_warm" else {}
        if pol == "keep_alive":
            params = {"ttl_s": 30}
        (await c.post(f"{self.stub}/admin/policy", json={"name": pol, "params": params})
         ).raise_for_status()
        if pol == "markov1":
            for k, calls in enumerate(train):
                r = await c.post(f"{self.stub}/decide", json={
                    "session_id": f"warmup-{k}", "now_ms": calls[-1]["t_ms"],
                    "history": history(calls)})
                r.raise_for_status()
            info["markov1_warmup_sessions"] = len(train)
        if pol == "always_warm":
            st = (await c.get(f"{self.ctl}/v1/state")).json()
            for s in st:
                await c.post(f"{self.ctl}/v1/warm", json={"tool_class": s, "copies": 1,
                                                          "ttl_s": 90, "reason": "always_warm"})
            t = time.monotonic()
            while time.monotonic() - t < 300:
                st = (await c.get(f"{self.ctl}/v1/state")).json()
                if all(v["ready"] >= 1 for v in st.values()):
                    break
                await asyncio.sleep(1)
            else:
                raise RuntimeError("always_warm: not every server became ready in 300 s")
            info["pool_ready_s"] = round(time.monotonic() - t, 1)
        return info

    async def model_requests(self, c: httpx.AsyncClient) -> float | None:
        try:
            text = (await c.get(f"{self.model}/metrics")).text
        except httpx.HTTPError:
            return None
        return metrics(text, ["skypiea_predictor_latency_seconds_count"])[
            "skypiea_predictor_latency_seconds_count"]

    async def policy(self, pol: str, sessions: dict[str, list[dict]], train: list[list[dict]],
                     profile: str | None) -> dict:
        async with httpx.AsyncClient(timeout=30) as c:
            waited = await self.wait_zero(c, 600)
            before = await self.snapshot(c)
            m_before = await self.model_requests(c) if pol == "predictive" else None
            t_start = time.monotonic()
            info = await self.setup(c, pol, train, profile)
            print(f"== {self.scenario} {pol}: start (waited {waited:.0f}s for 0 replicas) {info}",
                  flush=True)
            rows: list[dict] = []
            sem = asyncio.Semaphore(self.conc)
            await asyncio.gather(*[self.session(c, pol, sid, calls, sem, rows)
                                   for sid, calls in sessions.items()])
            t_sessions = time.monotonic() - t_start
            if pol != "predictive":
                await c.post(f"{self.stub}/admin/policy",
                             json={"name": "scale_to_zero", "params": {}})
            drain = await self.wait_zero(c, 600)
            after = await self.snapshot(c)
            m_after = await self.model_requests(c) if pol == "predictive" else None
        delta = {k: round(after[k] - before[k], 3) for k in before}
        ok = [r for r in rows if r["ok"]]
        added = [r["added_ms"] for r in ok]
        summ = {"dataset": self.dataset, "scenario": self.scenario, "policy": pol,
                "sessions": len(sessions),
                "calls": len(rows), "ok": len(ok), "cold": sum(1 for r in ok if r["cold"]),
                "cold_rate": round(sum(1 for r in ok if r["cold"]) / len(ok), 4) if ok else None,
                "predicted_calls": sum(1 for r in ok if r["predicted"]),
                "added_ms_p50": nearest_rank(added, 50), "added_ms_p99": nearest_rank(added, 99),
                "held_ms_max": max((r["held_ms"] for r in ok), default=None),
                "sessions_s": round(t_sessions, 1), "drain_s": round(drain, 1),
                "idle_pod_s": delta["skypiea_pod_seconds_idle_total"], "metrics": delta,
                "setup": info}
        if pol == "predictive":
            summ["model_decide_requests"] = (None if m_before is None or m_after is None
                                             else m_after - m_before)
        print("POLICY " + json.dumps(summ), flush=True)
        return summ


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in-cluster", action="store_true")
    ap.add_argument("--namespace", default="skypiea-tools")
    ap.add_argument("--base", help="URL prefix map for laptop use: gw,ctl,stub,model")
    ap.add_argument("--sessions-json", required=True)
    ap.add_argument("--scenario", required=True, choices=["A", "B"])
    ap.add_argument("--policies", required=True)
    ap.add_argument("--profile", help="expected model profile (predictive)")
    ap.add_argument("--concurrency", type=int, default=5)
    ap.add_argument("--limit", type=int, default=0, help="first K sessions only (kind smoke)")
    ap.add_argument("--dataset", default="real", choices=["real", "SYNTHETIC"],
                    help="label on every row: SYNTHETIC for the prd profile on PRD sessions")
    a = ap.parse_args()
    ns = a.namespace
    if a.base:
        gw, ctl, stub, model = a.base.split(",")
    else:
        gw, ctl, stub, model = (f"http://skypiea-gateway.{ns}.svc:80",
                                f"http://skypiea-controller.{ns}.svc:8081",
                                f"http://skypiea-predictor.{ns}.svc:80",
                                f"http://skypiea-model.{ns}.svc:80")
    data = json.loads(Path(a.sessions_json).read_text())[a.scenario]
    sessions = data["sessions"]
    if a.limit:
        sessions = dict(list(sessions.items())[:a.limit])
    pols = a.policies.split(",")
    bad = [p for p in pols if p not in STUB_POLICIES + ["predictive"]]
    if bad or ("predictive" in pols and len(pols) > 1) or ("predictive" in pols and not a.profile):
        raise SystemExit(f"policies {pols}: predictive runs alone (gateway serves the model) "
                         f"and needs --profile; unknown: {bad}")
    print(f"REAL-TIMING run: scenario {a.scenario}, dataset {a.dataset}, {len(sessions)} sessions, "
          f"{a.concurrency} at once, unscaled gaps, policies {pols}", flush=True)
    r = Runner(gw, ctl, stub, model, a.scenario, a.concurrency, time.monotonic())
    r.dataset = a.dataset
    failures = []
    for pol in pols:
        try:
            s = asyncio.run(r.policy(pol, sessions, data["train"], a.profile))
            if s["ok"] != s["calls"]:
                failures.append(f"{pol}: {s['calls'] - s['ok']} calls failed")
            if pol == "predictive" and not s.get("model_decide_requests"):
                failures.append(f"{pol}: the model saw no /decide requests "
                                f"(is the gateway serving the model?)")
        except Exception as e:  # noqa: BLE001 - recorded, next policy still runs
            print(f"POLICY_ERROR {pol}: {type(e).__name__}: {e}", flush=True)
            failures.append(f"{pol}: {type(e).__name__}: {e}")
    print("FAILURES: " + ("; ".join(failures) if failures else "none"), flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
