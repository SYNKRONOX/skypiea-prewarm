"""Gateway early connect, before/after on kind (demo measurement; the E0 harness cannot test
it: its probe calls Services directly, never through the gateway).

    python -m demo.early_connect_ab --label off --n 10      # gateway deployed with the flag off
    python -m demo.early_connect_ab --label on  --n 10      # ... with earlyConnect=true
    python -m demo.early_connect_ab --report                # table from both CSVs

Runs on the BASELINE stack (skypiea-base, stub policy scale_to_zero, so every call is a cold
start): for each tool, n times: wait until the controller reports every tool at 0 replicas,
then one MCP call through the gateway (127.0.0.1:18180), recording the gateway's added latency
(X-Skypiea-Added-Ms: hold + cold wait + proxying) and the gateway's early-connect counter.
Images are pre-pulled (cached starts). Writes results/early_connect/<label>.csv.
"""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
import time
from pathlib import Path

import httpx

from agent.mcp_client import McpClient

GW = "http://127.0.0.1:18180"
CTL = "http://127.0.0.1:18181"
OUT = Path("results/early_connect")
TOOLS = {"user_lookup": {"user_id": "priya_menon_1119"},
         "reservation_lookup": {"action": "lookup", "reservation_id": "R35033531"},
         "read_file": {"path": "src/minicalc/calc.py"},
         "run_tests": {"selector": "test_calc"}}
FIELDS = ["label", "tool", "i", "cold", "added_ms", "held_ms", "wall_ms", "early_used",
          "zero_wait_s", "ok"]


def early_count(http: httpx.Client) -> float:
    tot = 0.0
    for ln in http.get(f"{GW}/metrics").text.splitlines():
        if ln.startswith("skypiea_gateway_early_connects_total{"):
            tot += float(ln.rsplit(" ", 1)[1])
    return tot


def wait_zero(http: httpx.Client, timeout_s: float = 120) -> float:
    t = time.monotonic()
    while time.monotonic() - t < timeout_s:
        st = http.get(f"{CTL}/v1/state").json()
        if all(v["live"] == 0 and v["ready"] == 0 for v in st.values()):
            return time.monotonic() - t
        time.sleep(0.5)
    raise SystemExit("tools not at 0")


def run(label: str, n: int) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{label}.csv"
    http = httpx.Client(timeout=10)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for i in range(n):                       # tools interleaved, not in blocks
            for tool, args in TOOLS.items():
                z = wait_zero(http)
                time.sleep(1.0)                  # let the EndpointSlice settle after scale-down
                e0 = early_count(http)
                c = McpClient(f"{GW}/mcp")
                _, info = c.call_tool(tool, args)
                c.close()
                row = {"label": label, "tool": tool, "i": i, "cold": info.cold,
                       "added_ms": info.added_ms, "held_ms": info.held_ms,
                       "wall_ms": round(info.wall_ms, 1),
                       "early_used": early_count(http) > e0, "zero_wait_s": round(z, 1),
                       "ok": info.ok}
                w.writerow(row)
                f.flush()
                print(row, flush=True)
    return path


def report() -> str:
    rows = {}
    for p in sorted(OUT.glob("*.csv")):
        for r in csv.DictReader(p.open()):
            if r["ok"] == "True" and r["cold"] == "True":
                rows.setdefault((r["tool"], r["label"]), []).append(r)
    lines = ["DEMO MEASUREMENT on kind (laptop), not a result. Baseline stack, scale-to-zero, "
             "cached starts, sequential, tools interleaved. Gateway added latency of cold "
             "calls (ms).", "",
             "| tool | early connect | n cold | median | p95 (nearest rank) | min | early path "
             "used |", "|---|---|---|---|---|---|---|"]
    for (tool, label), rs in sorted(rows.items()):
        xs = sorted(float(r["added_ms"]) for r in rs)
        p95 = xs[min(len(xs), max(1, -(-95 * len(xs) // 100))) - 1]
        used = sum(r["early_used"] == "True" for r in rs)
        lines.append(f"| {tool} | {label} | {len(xs)} | {statistics.median(xs):,.0f} | "
                     f"{p95:,.0f} | {xs[0]:,.0f} | {used}/{len(rs)} |")
    md = "\n".join(lines) + "\n"
    (OUT / "report.md").write_text(md)
    return md


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--label")
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args(argv)
    if a.report:
        print(report())
        return 0
    if not a.label:
        ap.error("--label or --report")
    print(f"wrote {run(a.label, a.n)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
