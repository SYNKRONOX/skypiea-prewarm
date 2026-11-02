"""E0 in-cluster probe: one cold-start run of one tool, timed on ONE monotonic clock.

Runs inside the long-running e0-probe pod (Helm: e0Probe.enabled=true); the laptop
orchestrator (experiments/e0_cold_start.py) calls it via ``kubectl exec`` and parses the JSON
it prints. Doing the scale request, the pod watch and the MCP calls from one process inside
the cluster keeps laptop network noise and WSL clock steps out of the numbers, and avoids
Kubernetes' 1-second timestamp resolution.

    python probe.py run --tool user_lookup --namespace skypiea-tools [--warm 20]

All t_* values are milliseconds since t0 = just before the scale request.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import socket
import statistics
import sys
import threading
import time
import urllib.request

from kubernetes import client, config, watch
from mcp import Client  # imported up front: client import cost is not part of a cold start

PULLED_RX = re.compile(r"in ([\d.]+)(ms|s) \(([\d.]+)(ms|s) including waiting\)")
SIZE_RX = re.compile(r"Image size: (\d+) bytes")


def _ms(v: str, unit: str) -> float:
    return float(v) * (1.0 if unit == "ms" else 1000.0)


class Run:
    def __init__(self, tool: str, ns: str, warm: int, service_ms: int, timeout_s: float):
        self.tool, self.ns, self.warm, self.service_ms = tool, ns, warm, service_ms
        self.timeout_s = timeout_s
        self.k8s_name = tool.replace("_", "-")
        self.selector = f"skypiea.io/tool={tool}"
        self.svc_host = f"{self.k8s_name}.{ns}.svc.cluster.local"
        self.url = f"http://{self.svc_host}/mcp"
        self.core = client.CoreV1Api()
        self.apps = client.AppsV1Api()
        self.t0 = 0.0
        self.r: dict = {"tool": tool, "service_ms": service_ms}
        self.pod_ip: str | None = None
        self.done = threading.Event()
        self.lock = threading.Lock()

    def now(self) -> float:
        return round((time.monotonic() - self.t0) * 1000.0, 2)

    def mark(self, key: str, value=None) -> None:
        with self.lock:
            if key not in self.r:
                self.r[key] = self.now() if value is None else value

    # ---- pod watch: scheduled / ip / running / ready, observed on our clock ----
    def watch_pod(self) -> None:
        w = watch.Watch()
        try:
            for ev in w.stream(self.core.list_namespaced_pod, self.ns,
                               label_selector=self.selector, timeout_seconds=int(self.timeout_s)):
                pod = ev["object"]
                if pod.metadata.deletion_timestamp:
                    continue
                self.mark("t_pod_seen")
                self.mark("pod", pod.metadata.name)
                for c in pod.status.conditions or []:
                    if c.status != "True":
                        continue
                    if c.type == "PodScheduled":
                        self.mark("t_scheduled")
                        self.mark("node", pod.spec.node_name)
                    elif c.type == "Ready":
                        self.mark("t_ready")
                if pod.status.pod_ip and not self.pod_ip:
                    self.pod_ip = pod.status.pod_ip
                    self.mark("t_pod_ip")
                for cs in pod.status.container_statuses or []:
                    if cs.image_id:
                        self.mark("image_digest", cs.image_id)  # repo@sha256:... as pulled
                    if cs.state and cs.state.running:
                        self.mark("t_running_observed")
                if ("t_ready" in self.r and "image_digest" in self.r) or self.done.is_set():
                    break
        finally:
            w.stop()

    # ---- poll /healthz on the pod IP: exact app-ready time + process start time ----
    def poll_health(self) -> None:
        deadline = time.monotonic() + self.timeout_s
        while not self.done.is_set() and time.monotonic() < deadline:
            if not self.pod_ip:
                time.sleep(0.005)
                continue
            t_req = self.now()
            try:
                with urllib.request.urlopen(f"http://{self.pod_ip}:8080/healthz",
                                            timeout=0.5) as resp:
                    body = json.loads(resp.read())
            except OSError:
                time.sleep(0.01)
                continue
            t_ok = self.now()
            mid = (t_req + t_ok) / 2
            self.mark("t_health_ok", t_ok)
            self.mark("health_rtt_ms", round(t_ok - t_req, 2))
            self.mark("t_proc_start", round(mid - float(body["uptime_s"]) * 1000.0, 2))
            self.mark("app_startup_ms", round(float(body["startup_s"] or 0) * 1000.0, 2))
            return

    # ---- MCP calls through the Service ----
    async def call_once(self) -> float:
        t = time.monotonic()
        async with Client(self.url, mode="2026-07-28") as c:
            res = await c.call_tool(self.tool, {"service_ms": self.service_ms})
        if res.is_error:
            raise RuntimeError(res.content[0].text if res.content else "tool error")
        return (time.monotonic() - t) * 1000.0

    def tcp_open(self, timeout: float = 0.1) -> bool:
        """Can we open a TCP connection to the Service right now? A SYN sent while the Service
        has no ready endpoint may be dropped (not refused); without this gate one MCP attempt
        would sit in SYN retransmits for seconds and inflate first_call."""
        try:
            with socket.create_connection((self.svc_host, 80), timeout=timeout):
                return True
        except OSError:
            return False

    async def first_and_warm(self) -> None:
        attempts, gate_fails, last_err = 0, 0, ""
        log: list[list] = []  # [t_start_ms, duration_ms, outcome] per MCP attempt
        deadline = time.monotonic() + self.timeout_s
        while time.monotonic() < deadline:
            if not await asyncio.to_thread(self.tcp_open):
                gate_fails += 1
                await asyncio.sleep(0.01)
                continue
            attempts += 1
            t_a = self.now()
            try:
                ms = await asyncio.wait_for(self.call_once(), timeout=5)
                log.append([t_a, round(self.now() - t_a, 1), "ok"])
                self.mark("t_first_ok")
                self.r["first_call_attempt_ms"] = round(ms, 2)
                break
            except Exception as e:  # not routable yet: refused / reset / timeout / 503
                log.append([t_a, round(self.now() - t_a, 1), type(e).__name__])
                last_err = f"{type(e).__name__}: {e}"[:200]
                await asyncio.sleep(0.01)
        self.r["first_call_attempts"] = attempts
        self.r["tcp_gate_fails"] = gate_fails
        self.r["attempt_log"] = log[-10:]
        if "t_first_ok" not in self.r:
            self.r["error"] = f"no successful call within {self.timeout_s}s; last: {last_err}"
            return
        lat = [round(await self.call_once(), 2) for _ in range(self.warm)]
        self.r["warm_ms"] = lat
        if lat:
            self.r["warm_median_ms"] = round(statistics.median(lat), 2)
            self.r["warm_p95_ms"] = round(sorted(lat)[max(0, int(0.95 * len(lat)) - 1)], 2)

    def kubelet_events(self) -> None:
        pod = self.r.get("pod")
        if not pod:
            return
        evs = self.core.list_namespaced_event(
            self.ns, field_selector=f"involvedObject.name={pod}").items
        for e in evs:
            msg = e.message or ""
            if e.reason == "Pulled":
                self.r["pull_event"] = msg[:300]
                if "already present" in msg:
                    self.r["image_cached"] = True
                    self.r["pull_ms"] = 0.0
                elif m := PULLED_RX.search(msg):
                    self.r["image_cached"] = False
                    self.r["pull_ms"] = _ms(m.group(1), m.group(2))
                    self.r["pull_incl_wait_ms"] = _ms(m.group(3), m.group(4))
                if m := SIZE_RX.search(msg):
                    self.r["pulled_image_bytes"] = int(m.group(1))
            elif e.reason in ("Failed", "BackOff", "FailedScheduling", "Unhealthy"):
                self.r.setdefault("warnings", []).append(f"{e.reason}: {msg[:200]}")

    def scale(self, n: int) -> None:
        self.apps.patch_namespaced_deployment_scale(
            self.k8s_name, self.ns, {"spec": {"replicas": n}})

    def wait_gone(self) -> None:
        t = time.monotonic()
        while time.monotonic() - t < self.timeout_s:
            if not self.core.list_namespaced_pod(self.ns, label_selector=self.selector).items:
                self.r["scale_down_ms"] = round((time.monotonic() - t) * 1000.0, 1)
                return
            time.sleep(0.2)
        self.r["error"] = (self.r.get("error", "") + " pod not gone after scale-down").strip()

    def precheck(self) -> str | None:
        existing = self.core.list_namespaced_pod(self.ns, label_selector=self.selector).items
        return f"{len(existing)} pod(s) already exist; not at zero" if existing else None

    def execute(self, barrier: threading.Barrier | None = None) -> dict:
        threads = [threading.Thread(target=self.watch_pod, daemon=True),
                   threading.Thread(target=self.poll_health, daemon=True)]
        if barrier is not None:  # parallel scale-up: all tools leave the barrier together
            barrier.wait()
        self.t0 = time.monotonic()
        self.r["wall_t0"] = time.time()
        for th in threads:
            th.start()
        self.scale(1)
        self.mark("t_scale_ack")
        try:
            asyncio.run(self.first_and_warm())
        finally:
            for th in threads:
                th.join(timeout=5 if "t_first_ok" in self.r else 0.1)
            self.done.set()
            try:
                self.kubelet_events()
            finally:
                self.scale(0)
                self.wait_gone()
        return self.r


def warm_client() -> float:
    """One real HTTP MCP round trip against a throwaway local server, before any t0, so the
    probe's own first-use costs (lazy imports, HTTP client setup) never land in first_call."""
    import uvicorn
    from mcp.server.mcpserver import MCPServer

    srv = MCPServer("warmup")

    @srv.tool()
    def ping(service_ms: int = 0) -> dict:
        return {"ok": True}

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    app = srv.streamable_http_app(json_response=True, stateless_http=True)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    while not server.started:
        time.sleep(0.005)

    async def calls() -> float:
        t = time.monotonic()
        for _ in range(2):
            async with Client(f"http://127.0.0.1:{port}/mcp", mode="2026-07-28") as c:
                await c.call_tool("ping", {"service_ms": 0})
        return (time.monotonic() - t) * 1000

    ms = asyncio.run(calls())
    server.should_exit = True
    th.join(timeout=5)
    return round(ms, 1)


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--tool", required=True, action="append",
                   help="repeat to scale several tools up at the same moment (parallel)")
    r.add_argument("--namespace", default="skypiea-tools")
    r.add_argument("--warm", type=int, default=20)
    r.add_argument("--service-ms", type=int, default=0)
    r.add_argument("--timeout", type=float, default=180.0)
    sub.add_parser("ping")
    a = ap.parse_args()
    config.load_incluster_config()
    if a.cmd == "ping":
        print(json.dumps({"ok": True}))
        return
    warm_ms = warm_client()
    runs = [Run(t, a.namespace, a.warm, a.service_ms, a.timeout) for t in a.tool]
    for r in runs:
        r.r["probe_warmup_ms"] = warm_ms
    errs = {r.tool: e for r in runs if (e := r.precheck())}
    if errs:
        print(json.dumps([{"tool": t, "error": e} for t, e in errs.items()]))
        sys.exit(2)
    results: dict[str, dict] = {}
    if len(runs) == 1:
        results[runs[0].tool] = runs[0].execute()
    else:
        barrier = threading.Barrier(len(runs))

        def go(r: Run) -> None:
            try:
                results[r.tool] = r.execute(barrier)
            except Exception as e:  # keep the other runs' results
                results[r.tool] = {**r.r, "error": f"{type(e).__name__}: {e}"[:300]}

        ths = [threading.Thread(target=go, args=(r,)) for r in runs]
        for th in ths:
            th.start()
        for th in ths:
            th.join()
    out = [results[r.tool] for r in runs]
    print(json.dumps(out))
    sys.exit(0 if all("error" not in o for o in out) else 2)


if __name__ == "__main__":
    main()
