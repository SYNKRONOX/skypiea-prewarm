"""Kind integration: replay one Scenario A and one Scenario B session through the gateway under
each placeholder policy. FUNCTIONAL CHECK ONLY: the laptop is shared, so no latency printed here
is a result (all reported timings come from the AWS run).

    python -m gateway.replay_check [--policies all] [--sessions-dir data/processed]

On AWS it runs INSIDE the cluster as a Job (no laptop tunnel; infra/aws/scripts/replay-job.sh):
    python -m gateway.replay_check --export-sessions sessions.json     # laptop, needs pandas
    python replay_check.py --in-cluster --sessions-json sessions.json   # in the Job pod

Sessions are read (read-only) from the synthetic PRD traces: Scenario A = first
synthetic_branching session with the full order_lookup -> policy_check -> refund_issue chain,
Scenario B = first synthetic_deterministic session. Think gaps are scaled by --gap-scale (0.2).
Checks per policy: every call succeeds; scale_to_zero -> every call cold; always_warm -> no call
cold; predictive policies -> the controller received predict:* warm requests; and every tool
is back at 0 replicas afterwards (TTL + purge). The always-warm standing pool is one pod per
tool with a 90 s TTL (it simply expires; a smaller warm never shrinks an active pool).
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import httpx

POLICIES = ["scale_to_zero", "always_warm", "keep_alive", "markov1", "predictive", "oracle"]
META = {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {}}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def port_forward(ctx: str | None, ns: str, svc: str, remote: int):
    if ctx is None:   # in-cluster: the Service DNS name, no port-forward
        yield f"http://{svc}.{ns}.svc:{remote}"
        return
    port = free_port()
    p = subprocess.Popen(["kubectl", "--context", ctx, "-n", ns, "port-forward", f"svc/{svc}",
                          f"{port}:{remote}"], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        for _ in range(100):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.1)
        yield f"http://127.0.0.1:{port}"
    finally:
        p.terminate()
        p.wait(timeout=5)


def load_session(path: Path, want: list[str] | None) -> list[dict]:
    import pandas as pd

    d = pd.read_parquet(path, columns=["session_id", "tool", "t_offset_ms", "service_ms"])
    for _, g in d.groupby("session_id", sort=True):
        if want is None or list(g["tool"]) == want:
            g = g.sort_values("t_offset_ms")
            return [{"tool_class": r.tool, "t_ms": float(r.t_offset_ms),
                     "service_ms": float(r.service_ms)} for r in g.itertuples()]
    raise SystemExit(f"no session {want} in {path}")


def schedule(calls: list[dict], gap_scale: float) -> list[dict]:
    """Think gap before each call (end of previous -> start), scaled."""
    out, prev_end = [], None
    for c in calls:
        gap = 0.0 if prev_end is None else max(0.0, c["t_ms"] - prev_end) * gap_scale
        out.append({**c, "gap_ms": gap})
        prev_end = c["t_ms"] + c["service_ms"]
    return out


def tool_call(cls: str, i: int, service_ms: float) -> dict:
    return {"jsonrpc": "2.0", "id": i, "method": "tools/call",
            "params": {"name": cls, "arguments": {"service_ms": int(service_ms)}, "_meta": META}}


def wait_all_zero(ctl: str, timeout_s: float) -> float:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout_s:
        st = httpx.get(f"{ctl}/v1/state", timeout=5).json()
        if all(v["live"] == 0 and v["ready"] == 0 for v in st.values()):
            return time.monotonic() - t0
        time.sleep(1)
    raise SystemExit(f"tools not back at 0 after {timeout_s}s: "
                     f"{ {k: v for k, v in st.items() if v['live'] or v['ready']} }")


def wait_all_ready(ctl: str, timeout_s: float) -> None:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout_s:
        st = httpx.get(f"{ctl}/v1/state", timeout=5).json()
        if all(v["ready"] >= 1 for v in st.values()):
            return
        time.sleep(1)
    raise SystemExit("always_warm: not every tool became ready")


def metric_sum(ctl: str, name: str, match: str = "") -> float:
    text = httpx.get(f"{ctl}/metrics", timeout=5).text
    return sum(float(ln.rsplit(" ", 1)[1]) for ln in text.splitlines()
               if (ln.startswith(name + "{") or ln.startswith(name + " ")) and match in ln)


def predict_warms(ctl: str) -> float:
    return metric_sum(ctl, "skypiea_warm_requests_total", 'reason="predict"')


def replay(gw: str, calls: list[dict], label: str) -> list[dict]:
    sid = uuid.uuid4().hex
    rows = []
    with httpx.Client(timeout=300) as c:
        for i, call in enumerate(calls):
            time.sleep(call["gap_ms"] / 1000)
            nxt = calls[i + 1] if i + 1 < len(calls) else None
            hdr = {"Mcp-Session-Id": sid, "MCP-Protocol-Version": "2026-07-28",
                   "Mcp-Method": "tools/call", "Mcp-Name": call["tool_class"],
                   "accept": "application/json, text/event-stream"}
            if nxt:
                hdr["X-Skypiea-Oracle"] = json.dumps([{"tool_class": nxt["tool_class"],
                                                      "in_ms": nxt["gap_ms"]}])
            t = time.monotonic()
            r = c.post(f"{gw}/mcp", json=tool_call(call["tool_class"], i + 1, call["service_ms"]),
                       headers=hdr)
            body = r.json()
            rows.append({"scenario": label, "i": i, "tool_class": call["tool_class"],
                         "gap_ms": round(call["gap_ms"]), "ok": "result" in body
                         and not body["result"].get("isError"),
                         "cold": r.headers.get("X-Skypiea-Cold") == "1",
                         "held_ms": r.headers.get("X-Skypiea-Held-Ms"),
                         "functional_ms": round((time.monotonic() - t) * 1000)})
        c.delete(f"{gw}/mcp", headers={"Mcp-Session-Id": sid})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--context", default="kind-skypiea")
    ap.add_argument("--namespace", default="skypiea-tools")
    ap.add_argument("--policies", default="all")
    ap.add_argument("--sessions-dir", default=str(
        Path(__file__).resolve().parent.parent / "data" / "processed"))
    ap.add_argument("--gap-scale", type=float, default=0.2)
    ap.add_argument("--train-sessions", type=int, default=3)
    ap.add_argument("--out", default=None, help="write per-call JSON lines here (not results/)")
    ap.add_argument("--in-cluster", action="store_true",
                    help="use the Service DNS names directly (run as a Job in the namespace)")
    ap.add_argument("--sessions-json", help="Scenario A/B calls exported by --export-sessions")
    ap.add_argument("--export-sessions", metavar="FILE",
                    help="write the Scenario A/B calls (tool, offset, service_ms) and exit")
    a = ap.parse_args()
    if a.sessions_json:
        raw = json.loads(Path(a.sessions_json).read_text())
    else:
        sd = Path(a.sessions_dir)
        raw = {"A": load_session(sd / "synthetic_branching" / "calls.parquet",
                                 ["order_lookup", "policy_check", "refund_issue"]),
               "B": load_session(sd / "synthetic_deterministic" / "calls.parquet", None)}
    if a.export_sessions:
        Path(a.export_sessions).write_text(json.dumps(raw))
        print(f"wrote {a.export_sessions}: A {len(raw['A'])} calls, B {len(raw['B'])} calls")
        return 0
    scen = {k: schedule(v, a.gap_scale) for k, v in raw.items()}
    ctx = None if a.in_cluster else a.context
    policies = POLICIES if a.policies == "all" else a.policies.split(",")
    print("FUNCTIONAL CHECK ONLY: latencies below are not results.\n")
    failures, all_rows = [], []
    with port_forward(ctx, a.namespace, "skypiea-gateway", 80) as gw, \
            port_forward(ctx, a.namespace, "skypiea-controller", 8081) as ctl, \
            port_forward(ctx, a.namespace, "skypiea-predictor", 80) as pred:
        for pol in policies:
            waited = wait_all_zero(ctl, 300)
            params = {"ttl_s": 90} if pol == "always_warm" else {}
            r = httpx.post(f"{pred}/admin/policy", json={"name": pol, "params": params},
                           timeout=5)
            r.raise_for_status()
            if pol == "always_warm":   # the baseline's standing pool: one pod per tool
                for s in httpx.get(f"{ctl}/v1/state", timeout=5).json():
                    httpx.post(f"{ctl}/v1/warm", json={"tool_class": s, "copies": 1,
                                                       "ttl_s": 90, "reason": "always_warm"})
                wait_all_ready(ctl, 300)
            if pol in ("markov1", "predictive"):  # learn transitions online first (not checked)
                for _ in range(a.train_sessions):
                    for sc in scen.values():
                        replay(gw, [{**c, "gap_ms": 0} for c in sc], "train")
                wait_all_zero(ctl, 120)
            q_before = metric_sum(ctl, "skypiea_scaleups_queued_total")
            before = predict_warms(ctl)
            rows = []
            for label, calls in scen.items():
                rows += replay(gw, calls, label)
            n_pred = predict_warms(ctl) - before
            for row in rows:
                row["policy"] = pol
            all_rows += rows
            cold = [r["cold"] for r in rows]
            q_all = metric_sum(ctl, "skypiea_scaleups_queued_total")
            print(f"== {pol} (waited {waited:.0f}s for all tools at 0; predict warms: "
                  f"{n_pred:.0f}; scale-ups queued during the sessions: {q_all - q_before:.0f}, "
                  f"since start: {q_all:.0f})")
            for row in rows:
                print(f"   {row['scenario']} {row['i']} {row['tool_class']:13s} "
                      f"gap={row['gap_ms']:>5}ms ok={row['ok']} cold={row['cold']} "
                      f"held={row['held_ms']}")
            checks = {"all calls succeeded": all(r["ok"] for r in rows)}
            if pol == "scale_to_zero":
                checks["every call cold"] = all(cold)
                checks["no predict warm"] = n_pred == 0
            if pol == "always_warm":
                checks["no call cold"] = not any(cold)
            if pol in ("keep_alive", "markov1", "predictive", "oracle"):
                checks["controller got predict:* warms"] = n_pred > 0
            for k, v in checks.items():
                print(f"   check: {k}: {'PASS' if v else 'FAIL'}")
                if not v:
                    failures.append(f"{pol}: {k}")
        back = wait_all_zero(ctl, 180)
        print(f"\nall tools back at 0 replicas {back:.0f}s after the last session")
        httpx.post(f"{pred}/admin/policy", json={"name": "scale_to_zero", "params": {}})
    if a.out:
        Path(a.out).write_text("\n".join(json.dumps(r) for r in all_rows) + "\n")
    print("\nFAILURES: " + ("; ".join(failures) if failures else "none"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
