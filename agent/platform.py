"""Experiment harness around the agent (NOT used by the agent itself, which only talks to the
gateway's POST /mcp): sets the stand-in policy on the stub predictor, waits until every tool
is back at 0 replicas, and reads the controller's idle pod-seconds. HTTP only, through the
loopback port-forwards of demo/portforward.sh; nothing from gateway/ or controller/ is imported.

Policies: scale_to_zero and keep_alive (30 s) are the stub's stand-ins (the gateway must serve
the stub); predictive is the real predictor (the gateway must serve the model, profile prd).
The model's /decide request counter is read before and after every run, so a run whose gateway
served the wrong predictor is flagged.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

POLICIES = {"scale_to_zero": ("scale_to_zero", {}),
            "keep_alive": ("keep_alive", {"ttl_s": 30}),
            "keep_alive_30": ("keep_alive", {"ttl_s": 30}),
            "predictive": None}
CTL_METRICS = ["skypiea_pod_seconds_idle_total", "skypiea_scale_events_total",
               "skypiea_warm_requests_total"]
MODEL_COUNTER = "skypiea_predictor_latency_seconds_count"


def metric_sum(text: str, name: str) -> float:
    tot = 0.0
    for ln in text.splitlines():
        if ln.startswith("#"):
            continue
        if ln.split("{", 1)[0].split(" ", 1)[0] == name:
            tot += float(ln.rsplit(" ", 1)[1])
    return tot


class Platform:
    def __init__(self, controller: str, stub: str, model: str, timeout_s: float = 15.0):
        self.ctl = controller.rstrip("/")
        self.stub, self.model = stub.rstrip("/"), model.rstrip("/")
        self.http = httpx.Client(timeout=timeout_s)

    def live(self) -> dict[str, int]:
        st = self.http.get(f"{self.ctl}/v1/state").json()
        return {k: int(v["live"]) + int(v["ready"]) for k, v in st.items()}

    def wait_zero(self, timeout_s: float = 600) -> float:
        t = time.monotonic()
        while True:
            live = {k: v for k, v in self.live().items() if v}
            if not live:
                return time.monotonic() - t
            if time.monotonic() - t > timeout_s:
                raise RuntimeError(f"tools not at 0 after {timeout_s:.0f}s: {live}")
            time.sleep(1)

    def controller_metrics(self) -> dict[str, float]:
        text = self.http.get(f"{self.ctl}/metrics").text
        return {m: metric_sum(text, m) for m in CTL_METRICS}

    def model_requests(self) -> float | None:
        try:
            return metric_sum(self.http.get(f"{self.model}/metrics").text, MODEL_COUNTER)
        except httpx.HTTPError:
            return None

    def model_health(self) -> dict[str, Any]:
        return self.http.get(f"{self.model}/healthz").json()

    def set_policy(self, policy: str, profile: str) -> dict[str, Any]:
        if policy not in POLICIES:
            raise ValueError(f"policy {policy!r}: one of {sorted(POLICIES)}")
        if policy == "predictive":
            h = self.model_health()
            if h.get("status") != "ok" or h.get("profile") != profile:
                raise RuntimeError(f"model /healthz {h} is not profile {profile}")
            return {"model_version": h.get("model_version"), "profile": h.get("profile")}
        name, params = POLICIES[policy]
        self.http.post(f"{self.stub}/admin/policy",
                       json={"name": name, "params": params}).raise_for_status()
        return {"stub_policy": name, "params": params}

    def reset(self) -> None:
        self.http.post(f"{self.stub}/admin/policy", json={"name": "scale_to_zero", "params": {}})
