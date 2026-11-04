"""Live /decide contract check against the real predictor (services/predictor, feat/model).

    python -m gateway.decide_check --url http://127.0.0.1:18090 --profile support \
        --sessions-json ~/.cache/skypiea/scenario/sessions.json --scenario A

Sends every prefix of the scenario's drawn test sessions as a contract-v1 request and checks:
/healthz ok, profile as expected, cold_start_matches_bundle; every answer passes the gateway's
validator (gateway/contract.py parse_response); model_version names the profile; every warm
decision names a class the tool map routes to a server; at least one warm decision; wall time
under the gateway's predictor timeout (300 ms). Answers are counted by `mode` (ranker, ngram,
majority, prior, none). FUNCTIONAL CHECK: latencies on kind are not results.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections import Counter
from pathlib import Path

import httpx

from gateway.contract import ContractError, parse_response
from tools.common.toolmap import load


def history(calls: list[dict]) -> list[dict]:
    return [{"tool_class": c["tool_class"], "t_start_ms": c["t_ms"],
             "t_end_ms": c["t_ms"] + c["service_ms"], "error": False,
             "batch_id": c["batch_id"]} for c in calls]


def run(url: str, profile: str, sessions: dict[str, list[dict]], timeout_ms: float) -> dict:
    tm = load()
    out: dict = {"profile": profile, "requests": 0, "contract_errors": [], "unrouted": set(),
                 "modes": Counter(), "warm_decisions": 0, "wall_ms": []}
    with httpx.Client(timeout=5) as c:
        h = c.get(f"{url}/healthz").json()
        out["healthz"] = h
        for sid, calls in sessions.items():
            for k in range(1, len(calls) + 1):
                # hex id like the gateway's Mcp-Session-Id (a ':' in the id degrades the
                # aabdfd2 predictor to mode=none; reported to the model window)
                req = {"session_id": "chk-" + hashlib.sha256(sid.encode()).hexdigest()[:24],
                       "now_ms": calls[k - 1]["t_ms"]
                       + calls[k - 1]["service_ms"], "history": history(calls[:k])}
                t = time.perf_counter()
                r = c.post(f"{url}/decide", json=req)
                out["wall_ms"].append((time.perf_counter() - t) * 1000)
                out["requests"] += 1
                d = r.json()
                try:
                    resp = parse_response(d)
                except ContractError as e:
                    out["contract_errors"].append(f"{sid[:12]}#{k}: {e}")
                    continue
                out["modes"][d.get("mode", "?")] += 1
                out["model_version"] = resp.model_version
                for x in resp.decisions:
                    if x.warm:
                        out["warm_decisions"] += 1
                        if tm.server_for(x.tool_class) is None:
                            out["unrouted"].add(x.tool_class)
    w = sorted(out["wall_ms"])
    out["wall_ms_p50"] = w[len(w) // 2] if w else None
    out["wall_ms_max"] = w[-1] if w else None
    out["checks"] = {
        "healthz ok": h.get("status") == "ok",
        "profile matches": h.get("profile") == profile,
        "cold start file matches bundle": h.get("cold_start_matches_bundle") is True,
        "every answer passes the contract validator": not out["contract_errors"],
        "model_version names the profile": profile in str(out.get("model_version", "")),
        "every warm decision is routable": not out["unrouted"],
        "at least one warm decision": out["warm_decisions"] > 0,
        f"every answer under {timeout_ms:g} ms": bool(w) and w[-1] < timeout_ms,
    }
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--profile", required=True, choices=["coding", "support", "prd"])
    ap.add_argument("--sessions-json", required=True)
    ap.add_argument("--scenario", required=True, choices=["A", "B"])
    ap.add_argument("--timeout-ms", type=float, default=300)
    a = ap.parse_args()
    sessions = json.loads(Path(a.sessions_json).read_text())[a.scenario]["sessions"]
    o = run(a.url, a.profile, sessions, a.timeout_ms)
    print(f"/decide check, profile {a.profile}, scenario {a.scenario}: {o['requests']} requests, "
          f"model {o.get('model_version')}, modes {dict(o['modes'])}, warm decisions "
          f"{o['warm_decisions']}, wall ms p50 {o['wall_ms_p50']:.1f} max {o['wall_ms_max']:.1f} "
          f"(functional, not results)")
    print(f"  healthz: {o['healthz']}")
    for e in o["contract_errors"][:5]:
        print(f"  contract error: {e}")
    if o["unrouted"]:
        print(f"  unrouted classes: {sorted(o['unrouted'])}")
    for k, v in o["checks"].items():
        print(f"  check: {k}: {'PASS' if v else 'FAIL'}")
    return 0 if all(o["checks"].values()) else 1


if __name__ == "__main__":
    sys.exit(main())
