"""FR-6 / E5 check: reload the REAL predictor model while /decide requests are in flight.

    python -m services.predictor.reload_check [--out results/fr6_reload_check.json]

Starts the predictor service in this process (uvicorn on 127.0.0.1, a free port, the real
bundles under models/ and configs/cold_start.yaml), keeps THREADS client threads sending
/decide continuously, and performs three reloads (to the coding bundle, back to the support bundle, and the support
bundle again; each verified by the service's own warm-up p99 gate) while they run. For each
reload one extra "spanning" request uploads half its body, waits until the reload has
finished, then sends the rest: it is in flight for the whole reload. Recorded:

- per reload: the service's own `seconds` (fetch + build + verify + swap, measured in the
  service) and the client's wall time for POST /reload, against the 2 s budget;
- every /decide request whose [start, end] overlaps a reload window (in flight during the
  reload): its HTTP status and which model version answered it;
- the spanning request: started before / ended after the reload, its status and version;
- totals: requests sent, non-200 answers (must be 0).

Laptop CPU, one process; a measurement of this machine, not an AWS result.
"""

from __future__ import annotations

import argparse
import json
import platform
import secrets
import socket
import threading
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
BUDGET_S = 2.0
THREADS = 8
BUNDLES = ["lgbm-support-aabdfd2", "lgbm-coding-af00023", "lgbm-support-aabdfd2"]


def _req(i: int) -> dict:
    classes = ["user_lookup", "reservation_lookup", "flight_search", "reservation_modify"]
    hist = [{"tool_class": classes[k % 4], "t_start_ms": 1000.0 * k,
             "t_end_ms": 1000.0 * k + 50.0, "error": False, "batch_id": k}
            for k in range(1 + i % 4)]
    return {"session_id": f"{i:032x}"[-32:], "now_ms": 5000.0, "history": hist}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run(models: Path, cold: Path, threads: int = THREADS, settle_s: float = 0.8,
        bundles: list[str] | None = None) -> dict:
    import uvicorn

    from services.predictor.app import Settings, make_app

    bundles = bundles or BUNDLES
    token = secrets.token_hex(16)                     # never printed or saved
    settings = Settings(str(models / bundles[0]), str(cold), token, str(models), ())
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(make_app(settings), host="127.0.0.1", port=port,
                                           log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    base = f"http://127.0.0.1:{port}"
    t_wait = time.monotonic()
    while not server.started:
        if time.monotonic() - t_wait > 30:
            raise SystemExit("predictor did not start")
        time.sleep(0.05)
    start_version = httpx.get(f"{base}/healthz").json().get("model_version")

    stop = threading.Event()
    log: list[tuple[float, float, int, str | None]] = []
    lock = threading.Lock()

    def worker(w: int) -> None:
        with httpx.Client(timeout=10) as c:
            i = w
            while not stop.is_set():
                t0 = time.monotonic()
                try:
                    r = c.post(f"{base}/decide", json=_req(i))
                    code = r.status_code
                    ver = r.json().get("model_version") if code == 200 else None
                except httpx.HTTPError:
                    code, ver = -1, None
                with lock:
                    log.append((t0, time.monotonic(), code, ver))
                i += threads

    ws = [threading.Thread(target=worker, args=(w,), daemon=True) for w in range(threads)]
    for w in ws:
        w.start()
    reloads = []
    try:
        time.sleep(settle_s)
        with httpx.Client(timeout=30) as c:
            for b in bundles[1:] + bundles[:1]:
                span: dict = {}
                gate = threading.Event()
                st_th = threading.Thread(target=_spanning, args=(base, gate, span))
                st_th.start()
                time.sleep(0.1)                       # the spanning request is open now
                t0 = time.monotonic()
                r = c.post(f"{base}/reload", json={"model_uri": str(models / b)},
                           headers={"X-Admin-Token": token})
                t1 = time.monotonic()
                body = r.json()
                reloads.append({"bundle": b, "http": r.status_code, "ok": body.get("ok"),
                                "model_version": body.get("model_version"),
                                "previous_version": body.get("previous_version"),
                                "service_seconds": body.get("seconds"),
                                "client_wall_s": round(t1 - t0, 3),
                                "warmup_p99_ms": body.get("warmup_p99_ms"),
                                "_t0": t0, "_t1": t1})
                gate.set()                            # now let it finish its upload
                st_th.join(timeout=15)
                reloads[-1]["spanning_request"] = {
                    "started_before_reload": span.get("t0", 1e18) < t0,
                    "ended_after_reload": span.get("t1", 0) > t1,
                    "http": span.get("code"), "model_version": span.get("ver")}
                time.sleep(settle_s)
    finally:
        stop.set()
        for w in ws:
            w.join(timeout=15)
        server.should_exit = True
        th.join(timeout=15)

    for rl in reloads:
        inflight = [x for x in log if x[0] < rl["_t1"] and x[1] > rl["_t0"]]
        rl["in_flight_requests"] = len(inflight)
        rl["in_flight_non_200"] = sum(1 for x in inflight if x[2] != 200)
        rl["in_flight_versions"] = sorted({x[3] for x in inflight if x[3]})
        rl["started_before_ended_after"] = sum(1 for x in inflight
                                               if x[0] < rl["_t0"] and x[1] > rl["_t1"])
        rl["within_budget"] = (rl["service_seconds"] is not None
                               and rl["service_seconds"] < BUDGET_S
                               and rl["client_wall_s"] < BUDGET_S)
        del rl["_t0"], rl["_t1"]
    lat = sorted((x[1] - x[0]) * 1000 for x in log)
    return {"check": "FR-6/E5 live reload under load (real bundles, in-process service)",
            "label": "measured on this machine; not an AWS result",
            "date_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "machine": {"python": platform.python_version(), "cpu_count": _cpus(),
                        "platform": platform.platform(terse=True)},
            "budget_s": BUDGET_S, "client_threads": threads, "start_version": start_version,
            "reloads": reloads, "requests": len(log),
            "non_200": sum(1 for x in log if x[2] != 200),
            "decide_wall_ms_p50": round(lat[len(lat) // 2], 2) if lat else None,
            "decide_wall_ms_max": round(lat[-1], 2) if lat else None,
            "all_reloads_ok": all(r["ok"] for r in reloads),
            "all_within_budget": all(r["within_budget"] for r in reloads),
            "every_in_flight_request_served": all(
                r["in_flight_non_200"] == 0 and r["in_flight_requests"] > 0
                and r["spanning_request"]["http"] == 200
                and r["spanning_request"]["started_before_reload"]
                and r["spanning_request"]["ended_after_reload"] for r in reloads)}


def _spanning(base: str, gate: threading.Event, out: dict) -> None:
    """A /decide request whose body arrives in two parts: the second only after `gate`."""
    raw = json.dumps(_req(3)).encode()

    def body():
        yield raw[: len(raw) // 2]
        gate.wait(20)
        yield raw[len(raw) // 2:]
    out["t0"] = time.monotonic()
    try:
        r = httpx.post(f"{base}/decide", content=body(), timeout=30,
                       headers={"content-type": "application/json"})
        out["code"] = r.status_code
        out["ver"] = r.json().get("model_version") if r.status_code == 200 else None
    except httpx.HTTPError as e:
        out["code"] = f"error {type(e).__name__}"
    out["t1"] = time.monotonic()


def _cpus() -> int | None:
    import os
    return os.cpu_count()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", type=Path, default=ROOT / "models")
    ap.add_argument("--cold", type=Path, default=ROOT / "configs" / "cold_start.yaml")
    ap.add_argument("--threads", type=int, default=THREADS)
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args(argv)
    res = run(a.models, a.cold, a.threads)
    text = json.dumps(res, indent=2) + "\n"
    print(text, end="")
    if a.out:
        a.out.write_text(text)
    ok = res["all_reloads_ok"] and res["all_within_budget"] and \
        res["every_in_flight_request_served"]
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
