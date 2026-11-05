"""Open-loop trace replayer: real sessions through the Skypiea gateway.

    python -m replayer.replay --config replayer/configs/kind_functional.yaml

* Sessions come from a processed trace (``<processed>/<source>/calls.parquet``, the
  traces.schema columns: session_id, batch_id, t_offset_ms, service_ms, tool, has_timing, split).
* **Open loop across sessions**: session start times are drawn up front from the arrival
  process (poisson / fixed / all_at_once) and never wait for other sessions.
* **Closed loop inside a session**, like a real agent: a batch's calls are sent concurrently;
  the next batch starts the recorded think gap after the previous batch has *actually*
  returned (gap = next batch start - previous batch end, from the trace).
* Each call asks the tool for its recorded ``service_ms`` of real work.
* ``mode: results`` requires gap_scale = service_scale = 1 (real gaps). ``mode: functional``
  allows scaling for quick checks; every row records the mode and both scales.
* One CSV row per call: run, policy, session, tool, cold, added_latency_ms, hold_ms,
  predicted, client latency, ok/error (gateway response headers X-Skypiea-*).
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import random
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
META = {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {}}
FIELDS = ["run_id", "mode", "gap_scale", "service_scale", "git_commit", "policy", "session_id",
          "source_session", "batch_id", "position", "tool_class", "service_ms", "gap_ms",
          "t_send_ms", "latency_ms", "cold", "added_latency_ms", "hold_ms", "predicted", "ok",
          "error"]


@dataclass
class Call:
    tool_class: str
    service_ms: float


@dataclass
class Batch:
    batch_id: int
    calls: list[Call]
    gap_ms: float          # think gap before this batch (0 for the first)


@dataclass
class Session:
    source_id: str
    batches: list[Batch]


@dataclass
class Config:
    sessions_path: str
    n_sessions: int = 10
    split: str | None = "test"
    seed: int = 0
    arrival: dict[str, Any] = field(default_factory=lambda: {"mode": "poisson",
                                                             "rate_per_s": 0.2})
    mode: str = "results"
    gap_scale: float = 1.0
    service_scale: float = 1.0
    max_gap_ms: float | None = None      # cap very long idle gaps (recorded in the CSV)
    default_gap_ms: float = 1000.0       # traces without timing (tau-bench)
    policy: dict[str, Any] | None = None
    oracle_hints: bool = False           # send X-Skypiea-Oracle (oracle baseline only)
    end_sessions: bool = True            # DELETE each session at its end (gateway purges it)
    out: str = "results/replay/{run_id}.csv"
    request_timeout_s: float = 300.0

    @staticmethod
    def load(path: str | Path) -> Config:
        d = yaml.safe_load(Path(path).read_text())
        cfg = Config(**d)
        cfg.validate()
        return cfg

    def validate(self) -> None:
        if self.mode not in ("results", "functional"):
            raise ValueError("mode must be results or functional")
        if self.mode == "results" and (self.gap_scale != 1.0 or self.service_scale != 1.0):
            raise ValueError("results runs use real gaps: gap_scale and service_scale must be 1")
        if self.arrival.get("mode") not in ("poisson", "fixed", "all_at_once"):
            raise ValueError("arrival.mode must be poisson, fixed or all_at_once")


def load_sessions(cfg: Config) -> list[Session]:
    path = Path(cfg.sessions_path).expanduser()
    cols = ["session_id", "batch_id", "position_in_batch", "t_offset_ms", "service_ms", "tool",
            "has_timing", "split"]
    d = pd.read_parquet(path)
    d = d[[c for c in cols if c in d.columns]]
    if cfg.split and "split" in d.columns:
        d = d[d["split"] == cfg.split]
    ids = sorted(d["session_id"].unique())
    rng = random.Random(cfg.seed)
    chosen = rng.sample(ids, min(cfg.n_sessions, len(ids)))
    out = []
    for sid in chosen:
        g = d[d["session_id"] == sid].sort_values(["batch_id", "position_in_batch"])
        batches, prev_end = [], None
        for bid, b in g.groupby("batch_id", sort=True):
            timed = bool(b["has_timing"].all()) and b["t_offset_ms"].notna().all()
            start = float(b["t_offset_ms"].min()) if timed else None
            if prev_end is None:
                gap = 0.0
            elif timed and prev_end is not None and not pd.isna(prev_end):
                gap = max(0.0, start - prev_end)
            else:
                gap = cfg.default_gap_ms
            svc = b["service_ms"].fillna(0.0).astype(float)
            batches.append(Batch(int(bid), [Call(t, float(s)) for t, s in zip(b["tool"], svc,
                                                                               strict=True)],
                                 gap))
            prev_end = (start + float(svc.max())) if timed else float("nan")
        out.append(Session(str(sid), batches))
    return out


def arrivals(cfg: Config, n: int) -> list[float]:
    """Session start offsets (s) from the run start: open loop, drawn up front."""
    a, rng = cfg.arrival, random.Random(cfg.seed + 1)
    if a["mode"] == "all_at_once":
        return [0.0] * n
    if a["mode"] == "fixed":
        return [i * float(a["interval_s"]) for i in range(n)]
    t, out = 0.0, []
    for _ in range(n):
        out.append(t)
        t += rng.expovariate(float(a["rate_per_s"]))
    return out


def tool_call(cls: str, i: int, service_ms: float) -> dict:
    return {"jsonrpc": "2.0", "id": i, "method": "tools/call",
            "params": {"name": cls, "arguments": {"service_ms": int(round(service_ms))},
                       "_meta": META}}


class Replayer:
    def __init__(self, cfg: Config, gateway_url: str, client: httpx.AsyncClient | None = None,
                 policy_name: str = "unknown", git_commit: str = "unknown",
                 clock=time.monotonic, sleep=asyncio.sleep):
        self.cfg, self.url = cfg, gateway_url.rstrip("/")
        self.client = client or httpx.AsyncClient(limits=httpx.Limits(max_connections=1000))
        self.policy, self.commit = policy_name, git_commit
        self.clock, self.sleep = clock, sleep
        self.run_id = time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
        self.rows: list[dict[str, Any]] = []
        self.t0 = 0.0

    def _gap(self, ms: float) -> float:
        if self.cfg.max_gap_ms is not None:
            ms = min(ms, self.cfg.max_gap_ms)
        return ms * self.cfg.gap_scale

    async def _one(self, sid: str, src: str, batch: Batch, pos: int, call: Call, gap: float,
                   oracle: list[dict] | None, seq: int) -> None:
        svc = call.service_ms * self.cfg.service_scale
        hdr = {"Mcp-Session-Id": sid, "MCP-Protocol-Version": "2026-07-28",
               "Mcp-Method": "tools/call", "Mcp-Name": call.tool_class,
               "accept": "application/json, text/event-stream"}
        if oracle is not None:
            hdr["X-Skypiea-Oracle"] = json.dumps(oracle)
        t = self.clock()
        row = {"run_id": self.run_id, "mode": self.cfg.mode, "gap_scale": self.cfg.gap_scale,
               "service_scale": self.cfg.service_scale, "git_commit": self.commit,
               "policy": self.policy, "session_id": sid, "source_session": src,
               "batch_id": batch.batch_id, "position": pos, "tool_class": call.tool_class,
               "service_ms": round(svc, 1), "gap_ms": round(gap, 1),
               "t_send_ms": round((t - self.t0) * 1000, 1)}
        try:
            r = await self.client.post(f"{self.url}/mcp", json=tool_call(call.tool_class, seq, svc),
                                       headers=hdr, timeout=self.cfg.request_timeout_s)
            body = r.json()
            ok = "result" in body and not body["result"].get("isError")
            row.update(ok=ok, error=None if ok else json.dumps(body.get("error"))[:200],
                       cold=r.headers.get("X-Skypiea-Cold") == "1",
                       hold_ms=_f(r.headers.get("X-Skypiea-Held-Ms")),
                       added_latency_ms=_f(r.headers.get("X-Skypiea-Added-Ms")),
                       predicted=r.headers.get("X-Skypiea-Predicted") == "1")
        except (httpx.HTTPError, ValueError) as e:
            row.update(ok=False, error=f"{type(e).__name__}: {e}"[:200], cold=None,
                       hold_ms=None, added_latency_ms=None, predicted=None)
        row["latency_ms"] = round((self.clock() - t) * 1000, 1)
        self.rows.append(row)

    async def run_session(self, s: Session, start_at: float) -> None:
        await self.sleep(max(0.0, start_at - (self.clock() - self.t0)))
        sid = f"{self.run_id}-{uuid.uuid4().hex[:8]}"
        seq = 0
        for bi, b in enumerate(s.batches):
            gap = self._gap(b.gap_ms)
            if bi > 0:
                await self.sleep(gap / 1000.0)
            oracle = None
            if self.cfg.oracle_hints and bi + 1 < len(s.batches):
                nxt = s.batches[bi + 1]
                oracle = [{"tool_class": c.tool_class, "in_ms": self._gap(nxt.gap_ms)}
                          for c in nxt.calls]
            tasks = []
            for pos, c in enumerate(b.calls):
                seq += 1
                tasks.append(self._one(sid, s.source_id, b, pos, c, gap, oracle, seq))
            await asyncio.gather(*tasks)
        if not self.cfg.end_sessions:
            return
        try:
            await self.client.delete(f"{self.url}/mcp", headers={"Mcp-Session-Id": sid},
                                     timeout=10)
        except httpx.HTTPError:
            pass

    async def run(self, sessions: list[Session]) -> list[dict[str, Any]]:
        starts = arrivals(self.cfg, len(sessions))
        self.t0 = self.clock()
        await asyncio.gather(*(self.run_session(s, t) for s, t in zip(sessions, starts,
                                                                       strict=True)))
        return self.rows


def _f(v: str | None) -> float | None:
    try:
        return None if v is None else float(v)
    except ValueError:
        return None


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in sorted(rows, key=lambda r: r["t_send_ms"]):
            w.writerow({k: r.get(k) for k in FIELDS})


def git_commit() -> str:
    p = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
                       capture_output=True, text=True)
    dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--",
                            "replayer/", "gateway/"], capture_output=True, text=True).stdout
    return p.stdout.strip() + ("-dirty" if dirty.strip() else "")


def main() -> int:
    from skypiea_cli.backend import KubeBackend, port_forward

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--config", required=True)
    ap.add_argument("--gateway-url", help="skip port-forward and use this gateway URL")
    ap.add_argument("--kubeconfig")
    ap.add_argument("--context")
    ap.add_argument("--namespace", default="skypiea-tools")
    a = ap.parse_args()
    cfg = Config.load(a.config)
    sessions = load_sessions(cfg)
    if cfg.mode == "functional":
        print(f"FUNCTIONAL run (gap x{cfg.gap_scale}, service x{cfg.service_scale}): "
              "not results.")
    kube = KubeBackend(a.kubeconfig, a.context, a.namespace)
    policy = "unknown"
    if cfg.policy:
        kube.set_policy(cfg.policy["name"], cfg.policy.get("params") or {})
    policy = kube.get_policy().get("policy", policy)
    pf = None if a.gateway_url else port_forward(kube, "skypiea-gateway", 80)
    url = a.gateway_url or pf.__enter__()
    try:
        rep = Replayer(cfg, url, policy_name=policy, git_commit=git_commit())
        rows = asyncio.run(rep.run(sessions))
    finally:
        if pf is not None:
            pf.__exit__(None, None, None)
    out = (ROOT / Path(cfg.out.format(run_id=rep.run_id)).expanduser()).resolve()
    write_csv(rows, out)
    n_cold = sum(1 for r in rows if r.get("cold"))
    print(f"{len(rows)} calls from {len(sessions)} sessions, policy={policy}, "
          f"cold={n_cold}, failed={sum(1 for r in rows if not r.get('ok'))} -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
