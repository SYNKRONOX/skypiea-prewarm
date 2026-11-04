"""Load test of ``POST /decide`` (NFR-1 proxy).

Two modes:
- closed loop (``--concurrency N``): N clients, each sends its next request when the last
  answer arrives; at high N this measures queueing, not the service's own latency;
- open loop (``--rate R``): requests are sent at Poisson arrival times with mean rate R/s
  (seeded), whatever the answers do; latency is measured from the scheduled send time, so
  client lag counts against the service. 500 sessions x one call per ~6.7 s ~ 75 req/s.

    nice -n 10 python -m services.predictor.loadtest --model models/<version> \
        --cold-start PATH [--concurrency 200 --seconds 60]
    python -m services.predictor.loadtest --url http://127.0.0.1:8090 --model models/<v>

Without ``--url`` the service is started as a child process (``python -m services.predictor``,
``--workers`` uvicorn workers) on a free local port. Requests: with ``--source``, 500 decision
points sampled (seed 0) from that source's VAL sessions (histories up to 200 calls; metadata
only); otherwise the bundle's 20 golden requests, cycled. Reports client-side p50/p99/max
latency, error rate (non-200 or transport error), the service's own latency_ms p99, and
the child's RSS at start and end (memory growth); appends a row to results/loadtest.csv
with the git commit.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import random
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

from predictors.eval import RESULTS, git_commit
from sim.offline import _append

ROOT = Path(__file__).resolve().parents[2]


def _pct(xs: list[float], q: float) -> float:
    if not xs:
        return math.nan
    s = sorted(xs)
    return s[min(len(s) - 1, max(0, math.ceil(q * len(s)) - 1))]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def rss_kb(pid: int) -> int | None:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    except OSError:
        return None
    return None


async def _wait_ready(url: str, timeout_s: float = 60.0) -> None:
    async with httpx.AsyncClient() as c:
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout_s:
            try:
                if (await c.get(f"{url}/healthz")).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.2)
    raise RuntimeError("service did not become ready")


async def run_open(url: str, bodies: list[bytes], rate: float, seconds: float
                   ) -> dict[str, float]:
    rng = random.Random(0)
    lat: list[float] = []
    svc: list[float] = []
    errors = 0
    limits = httpx.Limits(max_connections=1000, max_keepalive_connections=200)
    async with httpx.AsyncClient(base_url=url, limits=limits, timeout=5.0) as c:
        async def one(body: bytes, t_sched: float) -> None:
            nonlocal errors
            try:
                r = await c.post("/decide", content=body,
                                 headers={"content-type": "application/json"})
                if r.status_code != 200:
                    errors += 1
                    return
                lat.append((time.perf_counter() - t_sched) * 1000.0)
                svc.append(float(r.json().get("latency_ms", math.nan)))
            except httpx.HTTPError:
                errors += 1

        tasks = []
        t0 = time.perf_counter()
        t, k = 0.0, 0
        while True:
            t += rng.expovariate(rate)
            if t > seconds:
                break
            delay = t0 + t - time.perf_counter()
            if delay > 0:
                await asyncio.sleep(delay)
            tasks.append(asyncio.create_task(one(bodies[k % len(bodies)], t0 + t)))
            k += 1
        await asyncio.gather(*tasks)
    n = len(lat) + errors
    return {"requests": n, "errors": errors, "error_pct": 100.0 * errors / n if n else math.nan,
            "rps": n / seconds, "p50_ms": _pct(lat, 0.5), "p99_ms": _pct(lat, 0.99),
            "max_ms": max(lat) if lat else math.nan, "service_p99_ms": _pct(svc, 0.99)}


def sample_requests(source: str, n: int = 500, seed: int = 0) -> list[dict[str, object]]:
    from predictors.eval import split_groups
    from services.predictor.engine import golden_requests
    _, val, _ = split_groups(source, "fixed")
    pts = [(i, j) for i, ep in enumerate(val) for j in range(1, ep.n_batches + 1)]
    rng = random.Random(seed)
    pick = sorted(rng.sample(pts, min(n, len(pts))))
    out = []
    for i, j in pick:
        ep = val[i]
        r = golden_requests([ep], j)[-1]
        r["session_id"] = f"load-{len(out)}"
        out.append(r)
    return out


async def run(url: str, bodies: list[bytes], concurrency: int, seconds: float
              ) -> dict[str, float]:
    lat: list[float] = []
    svc: list[float] = []
    errors = 0
    stop = time.monotonic() + seconds
    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    async with httpx.AsyncClient(base_url=url, limits=limits, timeout=5.0) as c:
        async def worker(i: int) -> None:
            nonlocal errors
            k = i
            while time.monotonic() < stop:
                body = bodies[k % len(bodies)]
                k += concurrency
                t0 = time.perf_counter()
                try:
                    r = await c.post("/decide", content=body,
                                     headers={"content-type": "application/json"})
                    dt = (time.perf_counter() - t0) * 1000.0
                    if r.status_code != 200:
                        errors += 1
                        continue
                    lat.append(dt)
                    svc.append(float(r.json().get("latency_ms", math.nan)))
                except httpx.HTTPError:
                    errors += 1

        await asyncio.gather(*(worker(i) for i in range(concurrency)))
    n = len(lat) + errors
    return {"requests": n, "errors": errors, "error_pct": 100.0 * errors / n if n else math.nan,
            "rps": n / seconds, "p50_ms": _pct(lat, 0.5), "p99_ms": _pct(lat, 0.99),
            "max_ms": max(lat) if lat else math.nan, "service_p99_ms": _pct(svc, 0.99)}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--url", default=None)
    p.add_argument("--model", required=True, help="bundle folder (requests: its golden.json)")
    p.add_argument("--cold-start", default=str(ROOT / "configs" / "cold_start.yaml"))
    p.add_argument("--concurrency", type=int, default=200)
    p.add_argument("--rate", type=float, default=None, help="open loop at this req/s")
    p.add_argument("--workers", type=int, default=1)
    p.add_argument("--source", default=None, help="sample requests from this source's VAL")
    p.add_argument("--seconds", type=float, default=60.0)
    a = p.parse_args(argv)
    if a.source:
        bodies = [json.dumps(r).encode() for r in sample_requests(a.source)]
    else:
        cases = json.loads((Path(a.model) / "golden.json").read_text(encoding="utf-8"))
        bodies = [json.dumps(c["request"]).encode() for c in cases]
    child = None
    url = a.url
    if url is None:
        port = _free_port()
        env = {**os.environ, "MODEL_PATH": str(Path(a.model).resolve()),
               "COLD_START_PATH": str(Path(a.cold_start).resolve()), "PORT": str(port),
               "HOST": "127.0.0.1", "WORKERS": str(a.workers)}
        env.pop("ADMIN_TOKEN", None)
        child = subprocess.Popen([sys.executable, "-m", "services.predictor"], env=env,
                                 cwd=str(ROOT))
        url = f"http://127.0.0.1:{port}"
    try:
        asyncio.run(_wait_ready(url))
        asyncio.run(run(url, bodies, min(a.concurrency, 20), 3.0))  # warm-up, not reported
        rss0 = rss_kb(child.pid) if child else None
        res = (asyncio.run(run_open(url, bodies, a.rate, a.seconds)) if a.rate
               else asyncio.run(run(url, bodies, a.concurrency, a.seconds)))
        rss1 = rss_kb(child.pid) if child else None
    finally:
        if child is not None:
            child.terminate()
            child.wait(timeout=10)
    row = {"time": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "git_commit": git_commit(),
           "model": Path(a.model).name, "mode": "open" if a.rate else "closed",
           "rate": a.rate or "", "concurrency": "" if a.rate else a.concurrency,
           "workers": a.workers, "requests_from": a.source or "golden", "seconds": a.seconds,
           **{k: round(v, 3) if isinstance(v, float) else v for k, v in res.items()},
           "rss_start_kb": rss0, "rss_end_kb": rss1}
    print(json.dumps(row, indent=1))
    _append(RESULTS / "loadtest.csv", [row])
    return 0


if __name__ == "__main__":
    sys.exit(main())
