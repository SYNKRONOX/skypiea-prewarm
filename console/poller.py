"""Polls both controllers (every 0.25 s) and the slower sources (every 2 s), keeps the pod grid
and the samples used for start contention, and publishes them to the browser.

Pod grid states come from each controller's /v1/state (its live view of the Deployments):
  cold     = 0 replicas
  starting = replicas > ready (a pod was requested and is not Ready yet)
  warm     = ready > 0
"warm for" is measured by the console from the first poll that saw ready > 0 (resolution
0.25 s) - it is not inferred from TTLs.

Start contention for a call = the largest number of pods starting at the same time on the
node (both stacks: the two controllers each allow 2 concurrent starts, so up to 4) among the
samples taken while that call was in flight, plus scale-ups waiting in a controller queue.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any

from console.stacks import StackClient


class Poller:
    def __init__(self, clients: dict[str, StackClient], publish, kube_health=None,
                 period_s: float = 0.25, slow_s: float = 2.0, clock=time.monotonic):
        self.clients, self.publish, self.kube_health = clients, publish, kube_health
        self.period_s, self.slow_s, self.clock = period_s, slow_s, clock
        self.grid: dict[str, dict[str, dict[str, Any]]] = {k: {} for k in clients}
        self.warm_since: dict[tuple[str, str], float] = {}
        self.samples: deque = deque(maxlen=20000)   # (t, {stack: starting}, {stack: queued})
        self.idle: dict[str, dict[str, float]] = {k: {} for k in clients}
        self.errors: dict[str, str | None] = dict.fromkeys(clients)
        self.model: dict[str, Any] = {}
        self.policy: dict[str, Any] = {}
        self.links: dict[str, str] = {}       # gateway reachability (port-forward health)
        self._last_slow = 0.0
        # called after every slow poll (fresh idle counters): the console re-publishes the
        # conversation's totals there, so idle pod-seconds accrued BETWEEN and AFTER calls
        # (LLM think time, the human's turn, the TTL tail) are shown, not only at call ends
        self.on_slow = None

    # ---------------- pure helpers (tested offline) ----------------
    @staticmethod
    def cell(v: dict[str, Any]) -> str:
        live, ready = int(v.get("live", 0)), int(v.get("ready", 0))
        if ready > 0:
            return "warm"
        if live > 0 or int(v.get("queued", 0)) > 0:
            return "starting"
        return "cold"

    def ingest(self, key: str, state: dict[str, Any], now: float) -> dict[str, dict]:
        grid = {}
        for server, v in sorted(state.items()):
            c = self.cell(v)
            k = (key, server)
            if c == "warm":
                self.warm_since.setdefault(k, now)
            else:
                self.warm_since.pop(k, None)
            grid[server] = {"state": c, "replicas": int(v.get("live", 0)),
                            "ready": int(v.get("ready", 0)), "queued": int(v.get("queued", 0)),
                            "warm_for_s": round(now - self.warm_since[k], 1)
                            if k in self.warm_since else 0.0,
                            "ttl_left_s": v.get("warm_for_s", 0.0),
                            "inflight": int(v.get("inflight", 0))}
        self.grid[key] = grid
        return grid

    def record_sample(self, now: float) -> None:
        starting = {k: sum(max(0, c["replicas"] - c["ready"]) for c in g.values())
                    for k, g in self.grid.items()}
        queued = {k: sum(c["queued"] for c in g.values()) for k, g in self.grid.items()}
        self.samples.append((now, starting, queued))

    def contention(self, t0: float, t1: float) -> dict[str, Any]:
        """Max pods starting at once on the node while [t0, t1] (both stacks), n samples."""
        win = [s for s in self.samples if t0 - self.period_s <= s[0] <= t1]
        if not win:
            return {"max_starting": None, "max_queued": None, "samples": 0}
        return {"max_starting": max(sum(s[1].values()) for s in win),
                "max_starting_by_stack": {k: max(s[1].get(k, 0) for s in win)
                                          for k in self.clients},
                "max_queued": max(sum(s[2].values()) for s in win), "samples": len(win)}

    def all_zero(self) -> bool:
        return all(c["replicas"] == 0 and c["queued"] == 0
                   for g in self.grid.values() for c in g.values()) and \
            all(self.grid.values())

    def max_ttl_left(self) -> float:
        return max((c["ttl_left_s"] for g in self.grid.values() for c in g.values()),
                   default=0.0)

    # ---------------- loops ----------------
    async def tick(self) -> None:
        now = self.clock()
        for key, cl in self.clients.items():
            try:
                self.ingest(key, await cl.state(), now)
                self.errors[key] = None
            except Exception as e:  # noqa: BLE001 - shown on screen, never fatal
                self.errors[key] = f"{type(e).__name__}"
        self.record_sample(now)
        await self.publish({"type": "pods", "grid": self.grid, "errors": self.errors})
        if now - self._last_slow >= self.slow_s:
            self._last_slow = now
            await self.refresh_idle()
            if self.kube_health is not None:
                self.model = await self.kube_health()
            try:
                self.policy = await self.clients["base"].policy()
            except Exception:  # noqa: BLE001
                self.policy = {"error": "unreachable"}
            for key, cl in self.clients.items():
                try:
                    r = await cl.http.get(f"{cl.s.gateway}/healthz", timeout=2)
                    self.links[key] = "ok" if r.status_code == 200 else f"http {r.status_code}"
                except Exception as e:  # noqa: BLE001
                    self.links[key] = f"down ({type(e).__name__})"
            await self.publish({"type": "slow", "idle": self.idle, "model": self.model,
                                "baseline_policy": self.policy, "links": self.links})
            if self.on_slow is not None:
                await self.on_slow()

    async def refresh_idle(self) -> None:
        """Read both controllers' idle pod-second counters now."""
        for key, cl in self.clients.items():
            try:
                self.idle[key] = await cl.idle_pod_s()
            except Exception:  # noqa: BLE001
                pass

    async def run(self) -> None:
        while True:
            t = self.clock()
            try:
                await self.tick()
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(max(0.0, self.period_s - (self.clock() - t)))
