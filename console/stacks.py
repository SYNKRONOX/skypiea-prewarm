"""The two stacks the Live Console observes, and read-only access to their state.

Everything here is HTTP to the platform's own endpoints through the loopback port-forwards of
demo/live_stacks.sh (controller /v1/state and /metrics, gateway /v1/sessions/<sid> - the same
data sources as `skypiea trace` / `explain` -, stub predictor /admin/policy, model /healthz).
The console never sits on a tool call's path: the agent calls the gateways itself and the
console only reads what the platform reports.
"""

from __future__ import annotations

import asyncio
import subprocess
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class Stack:
    key: str            # "base" | "pred"
    namespace: str
    gateway: str        # http://127.0.0.1:18180
    controller: str
    stub: str
    model: str | None   # only the predictive stack serves the real predictor

    @property
    def mcp_url(self) -> str:
        return f"{self.gateway}/mcp"


BASE = Stack("base", "skypiea-base", "http://127.0.0.1:18180", "http://127.0.0.1:18181",
             "http://127.0.0.1:18182", None)
PRED = Stack("pred", "skypiea-pred", "http://127.0.0.1:18280", "http://127.0.0.1:18281",
             "http://127.0.0.1:18282", "http://127.0.0.1:18283")

BASELINE_POLICIES = {"scale_to_zero": ("scale_to_zero", {}),
                     "keep_alive_30": ("keep_alive", {"ttl_s": 30})}


def metric_by_label(text: str, name: str, label: str) -> dict[str, float]:
    """{label value: sum} for one metric in Prometheus text format."""
    out: dict[str, float] = {}
    for ln in text.splitlines():
        if ln.startswith("#") or not ln.startswith(name):
            continue
        head, _, val = ln.rpartition(" ")
        if head.split("{", 1)[0] != name:
            continue
        key = ""
        if "{" in head:
            for kv in head.split("{", 1)[1].rstrip("}").split(","):
                k, _, v = kv.partition("=")
                if k == label:
                    key = v.strip('"')
        out[key] = out.get(key, 0.0) + float(val)
    return out


class StackClient:
    def __init__(self, stack: Stack, http: httpx.AsyncClient | None = None):
        self.s = stack
        self.http = http or httpx.AsyncClient(timeout=5)

    async def state(self) -> dict[str, Any]:
        return (await self.http.get(f"{self.s.controller}/v1/state")).json()

    async def idle_pod_s(self) -> dict[str, float]:
        text = (await self.http.get(f"{self.s.controller}/metrics")).text
        return metric_by_label(text, "skypiea_pod_seconds_idle_total", "tool")

    async def session(self, sid: str) -> dict[str, Any] | None:
        r = await self.http.get(f"{self.s.gateway}/v1/sessions/{sid}")
        return r.json() if r.status_code == 200 else None

    async def policy(self) -> dict[str, Any]:
        return (await self.http.get(f"{self.s.stub}/admin/policy")).json()

    async def set_policy(self, key: str) -> dict[str, Any]:
        name, params = BASELINE_POLICIES[key]
        r = await self.http.post(f"{self.s.stub}/admin/policy",
                                 json={"name": name, "params": params})
        r.raise_for_status()
        return r.json()

    async def model_health(self) -> dict[str, Any]:
        if not self.s.model:
            return {"status": "none"}
        try:
            r = await self.http.get(f"{self.s.model}/healthz", timeout=2)
            return r.json()
        except (httpx.HTTPError, ValueError) as e:
            return {"status": "unreachable", "error": type(e).__name__}

    async def hint(self, sid: str, turn: int) -> int:
        r = await self.http.post(f"{self.s.gateway}/v1/hint", json={"turn": turn},
                                 headers={"Mcp-Session-Id": sid})
        return r.status_code


async def kubectl(*args: str, context: str = "kind-skypiea") -> tuple[int, str]:
    """Run kubectl (scale the predictor down/up). Returns (rc, last output line)."""
    def run() -> tuple[int, str]:
        p = subprocess.run(["kubectl", "--context", context, *args], capture_output=True,
                           text=True, timeout=120)
        out = (p.stdout + p.stderr).strip().splitlines()
        return p.returncode, (out[-1] if out else "")
    return await asyncio.to_thread(run)
