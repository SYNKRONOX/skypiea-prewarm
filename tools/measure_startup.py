"""Measure each tool image's size and startup time with plain Docker (no Kubernetes).

    python tools/measure_startup.py [--n 5] [--tools a,b] [--tag TAG]

Per run: ``docker run -d`` -> poll /healthz on the published port every 10 ms.
Reports two numbers per tool (median over n runs, plus min/max):
  * app_startup_ms: process start -> HTTP server ready, measured inside the container
    (the ``startup_s`` the server reports on /healthz);
  * run_to_healthz_ms: ``docker run`` issued -> first /healthz 200, seen from the host
    (adds container creation and start).
Writes results/tool_startup.csv (one row per run).
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import subprocess
import time
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def sh(*args: str) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout.strip()


def one_run(image: str, timeout_s: float = 60.0) -> tuple[float, float]:
    t0 = time.monotonic()
    cid = sh("docker", "run", "-d", "--rm", "-p", "127.0.0.1::8080", image)
    try:
        port = sh("docker", "port", cid, "8080/tcp").splitlines()[0].rsplit(":", 1)[1]
        url = f"http://127.0.0.1:{port}/healthz"
        while time.monotonic() - t0 < timeout_s:
            try:
                with urllib.request.urlopen(url, timeout=0.5) as r:
                    body = json.loads(r.read())
                t_ok = time.monotonic()
                return (t_ok - t0) * 1000, float(body["startup_s"]) * 1000
            except Exception:
                time.sleep(0.01)
        raise TimeoutError(f"{image}: /healthz not OK after {timeout_s}s")
    finally:
        subprocess.run(["docker", "rm", "-f", cid], capture_output=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--tools", default="all")
    ap.add_argument("--tag", default=None)
    a = ap.parse_args()
    tag = a.tag or sh(str(ROOT / "tools" / "image_tag.sh"))
    servers = yaml.safe_load((ROOT / "tools" / "tool_map.yaml").read_text())["servers"]
    tools = list(servers) if a.tools == "all" else a.tools.split(",")

    out = ROOT / "results" / "tool_startup.csv"
    rows = []
    print(f"{'tool':18s} {'tier':6s} {'size MB':>8s} {'app_startup ms':>22s} "
          f"{'docker run->healthz ms':>24s}")
    for t in tools:
        image = f"skypiea/{t}:{tag}"
        size = int(sh("docker", "image", "inspect", image, "--format", "{{.Size}}"))
        runs = [one_run(image) for _ in range(a.n)]
        for i, (total, app) in enumerate(runs):
            rows.append({"tool": t, "tier": servers[t]["tier"], "image": image,
                         "image_bytes": size, "run": i, "app_startup_ms": round(app, 1),
                         "run_to_healthz_ms": round(total, 1)})
        app = [r[1] for r in runs]
        tot = [r[0] for r in runs]
        print(f"{t:18s} {servers[t]['tier']:6s} {size / 1e6:8.0f} "
              f"{statistics.median(app):8.0f} [{min(app):5.0f}-{max(app):5.0f}] "
              f"{statistics.median(tot):10.0f} [{min(tot):5.0f}-{max(tot):5.0f}]")
    out.parent.mkdir(exist_ok=True)
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {out.relative_to(ROOT)} ({len(rows)} runs)")


if __name__ == "__main__":
    main()
