"""Offline trace simulation of the warm pool (Phase 6).

    nice -n 10 python -m sim.offline --cold-start PATH   # all sources in configs/model/sim.yaml
    python -m sim.offline --sources tau2_bench --cold-start PATH
    python -m sim.offline --figures                      # figures + markdown from the CSVs

Test-split sessions with timing are replayed one by one through each policy, with their
recorded think times and service times. Cold-start durations come from the cold-start file
(``--cold-start``, default ``cold_start_path`` in configs/model/sim.yaml); it is read at run
time, its environment/status label and sha256 go into every row, and no cold-start number
lives in code. Swap in the AWS file by passing its path.

Model of the pool (definitions used for every number):
- Pods are per (session, server); sessions do not share pods. Tool classes map to servers by
  the cold-start file. A pod serves ``pod_concurrency`` call(s) at a time.
- Time 0 is the session start; batch b arrives at its ``t_offset_ms``; a call is busy for its
  ``service_ms``. The decision point after batch b is at its end (start + slowest call), when
  the gateway sends the request; there is none before the first batch.
- A call is **warm** if a Ready, free pod exists on arrival (added latency 0). If a pod for its
  server is still starting, the call waits for it (FR-9 hold; added latency = rest of the
  start). Otherwise it cold-starts a pod (added latency = the server's cold start). A pod
  scheduled for later is started at once.
- A pod expires at max(Ready, last use) + TTL and is removed at the next ``tick_ms`` tick
  (FR-3 proxy: removal within TTL + ``purge_ms``). A decision's TTL holds a prewarmed pod
  until its first use and can extend (never shorten) a live pod; after a use every pod keeps
  the policy's reactive TTL (the controller default, 30 s, for the predictive policies).
  A pod lives from its start to its removal; **idle** = Ready but not serving;
  startup = start -> Ready.
- **Hideable** call: not in the first batch and its think gap (decision point -> arrival) is
  at least its server's cold start in this mode. Only hideable calls can be warm under a
  just-in-time oracle that starts pods no earlier than the request.
- Cost (PRD cost model): CPU-seconds of every pod's life (startup, serving, idle) x its tier's
  CPU request x the vCPU-second price (configs/model/pricing.yaml, assumptions), per 1,000
  calls. NFR-3 proxy: CPU-seconds of pods never used / always-warm total CPU-seconds.
- FR-2 proxy (per call): among calls whose class the last decision warmed with prob >= tau
  and that were hideable, the share warm on arrival. Per session: sessions with at least one
  such call where (a) all, (b) >= 80% of them were warm.

Policies: scale_to_zero (TTL 0), always_warm (one pod per server of the source for the whole
session), keep_alive_<s>, markov1_top1 (SpecBox-style: top-1 Markov-1 next tool if >= tau,
1 copy, now, default TTL), ours_tau<t> (LightGBM ranker, raw scores, predictors.decide),
ours_now_tau<t> (same, but start now with the default TTL instead of the timing rule),
ours_cost (cost-based tau_j), oracle (warms the next batch's servers just in time from the
decision point: start = max(decision, arrival - cold start); removed after use).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from predictors.base import ContextPolicy, Decision, NextToolModel
from predictors.baselines import Markov
from predictors.cold_start import ColdStarts
from predictors.data import Context, Episode
from predictors.decide import DecideConfig, Decider, apply_caps, load_pricing, merge_by_server
from predictors.eval import RESULTS, git_commit, split_groups
from predictors.ladder import Ladder
from predictors.lgbm_ranker import LGBMRanker

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "configs" / "model" / "sim.yaml"
CSV_PATH = RESULTS / "sim_offline.csv"
HIDE_CSV = RESULTS / "sim_hideable.csv"


# --------------------------------------------------------------------------- pool
@dataclass
class Pod:
    server: str
    start: float
    ready: float
    expire: float
    ttl: float  # applied after each use
    prewarm: bool
    busy_until: float = -math.inf
    busy_ms: float = 0.0
    used: bool = False
    last_use: float | None = None
    fixed_end: float | None = None  # always-warm pods: alive until the session ends


@dataclass
class Spec:
    """One policy run: how it reacts to cold calls and what it does at decision points."""

    name: str
    reactive_ttl_ms: float
    tau: float | None = None
    policy: ContextPolicy | None = None
    always_warm: bool = False
    oracle: bool = False


@dataclass
class Stats:
    calls: int = 0
    warm: int = 0
    batches: int = 0
    batches_warm: int = 0
    added: list[float] = field(default_factory=list)
    hide_calls: int = 0
    hide_cold: int = 0
    nohide_calls: int = 0
    nohide_cold: int = 0
    pod_ms: float = 0.0
    idle_ms: float = 0.0
    startup_ms: float = 0.0
    cpu_s: float = 0.0
    unused_cpu_s: float = 0.0
    prewarm: int = 0
    prewarm_used: int = 0
    fr2_calls: int = 0
    fr2_ready: int = 0
    fr2_sessions: int = 0
    fr2_all: int = 0
    fr2_80: int = 0
    fr3_max_lag_ms: float = 0.0
    decisions: int = 0
    abstain: int = 0


class Env:
    """Cold starts, prices and simulation parameters for one cold mode."""

    def __init__(self, cold: ColdStarts, mode: str, sim: dict[str, Any],
                 pricing: dict[str, Any], decide: DecideConfig) -> None:
        self.cold, self.mode, self.sim, self.pricing, self.decide = cold, mode, sim, pricing, decide
        self.tick = float(sim["tick_ms"])
        self.conc = int(sim["pod_concurrency"])
        self.stat = str(sim["cold_stat"])
        self._cpu = {s: float(pricing["cpu_request_cores"][cold.server_tier(s)])
                     for s in cold.servers}

    def cold_ms(self, server: str) -> float:
        return self.cold.server_ms(server, self.mode, self.stat)

    def cpu(self, server: str) -> float:
        return self._cpu[server]

    def removal(self, expire: float) -> float:
        return math.ceil(expire / self.tick) * self.tick


def batch_times(ep: Episode) -> list[tuple[float, float]]:
    """(arrival, end) per batch: end = start + slowest call."""
    out = []
    for lo, hi in ep.batches:
        evs = ep.events[lo:hi]
        t = float(evs[0].t_offset_ms or 0.0)
        svc = [e.service_ms for e in evs if e.service_ms is not None]
        out.append((t, t + (max(svc) if svc else 0.0)))
    return out


def simulate(ep: Episode, spec: Spec, env: Env, servers: Sequence[str], st: Stats) -> None:
    cold = env.cold
    times = batch_times(ep)
    t_end = times[-1][1]
    pods: dict[str, list[Pod]] = defaultdict(list)
    done: list[Pod] = []
    if spec.always_warm:
        t0 = min(0.0, times[0][0])
        for s in servers:
            pods[s].append(Pod(s, t0, t0, t_end, 0.0, False, fixed_end=t_end))

    def alive(s: str, t: float) -> list[Pod]:
        """Live pods of ``s`` at ``t``; expired ones move to ``done``."""
        live = [p for p in pods[s] if p.expire >= t]
        done.extend(p for p in pods[s] if p.expire < t)
        pods[s] = live
        return live

    last: Decision | None = None
    fr2_n = fr2_ok = 0
    for b, (lo, hi) in enumerate(ep.batches):
        t, _ = times[b]
        gap = t - times[b - 1][1] if b > 0 else -math.inf
        all_warm = True
        for e in ep.events[lo:hi]:
            s = cold.server(e.tool)
            cms = env.cold_ms(s)
            live = alive(s, t)
            free = [p for p in live if p.busy_until <= t and p.start <= t]
            ready = [p for p in free if p.ready <= t]
            if ready:
                pod, wait = min(ready, key=lambda p: p.ready), 0.0
            elif free:
                pod = min(free, key=lambda p: p.ready)
                wait = pod.ready - t
            else:
                later = [p for p in live if p.start > t]
                if later:
                    pod = min(later, key=lambda p: p.start)
                    pod.start, pod.ready = t, t + cms
                else:
                    pod = Pod(s, t, t + cms, t + cms, spec.reactive_ttl_ms, False)
                    pods[s].append(pod)
                wait = cms
            serve = t + wait
            svc = float(e.service_ms or 0.0)
            pod.busy_until = serve + svc if env.conc == 1 else serve
            pod.busy_ms += svc
            pod.used, pod.last_use = True, serve + svc
            if pod.fixed_end is None:
                pod.expire = max(pod.expire, serve + svc + pod.ttl)
            warm = wait <= 0.0
            st.calls += 1
            st.warm += warm
            st.added.append(wait)
            all_warm &= warm
            hide = b > 0 and gap >= cms
            if hide:
                st.hide_calls += 1
                st.hide_cold += not warm
            else:
                st.nohide_calls += 1
                st.nohide_cold += not warm
            if (spec.tau is not None and hide and last is not None
                    and e.tool in last.warm and last.warm[e.tool].prob >= spec.tau):
                fr2_n += 1
                fr2_ok += warm
        st.batches += 1
        st.batches_warm += all_warm

        # decision point at the end of this batch
        d = times[b][1]
        if spec.oracle and b + 1 < ep.n_batches:
            t_next = times[b + 1][0]
            need = Counter(cold.server(x) for x in ep.batch_tools(b + 1))
            for s, n in sorted(need.items()):
                have = len([p for p in alive(s, d)
                            if p.expire >= t_next and p.busy_until <= t_next])
                for _ in range(n - have):
                    start = max(d, t_next - env.cold_ms(s))
                    ready = start + env.cold_ms(s)
                    pods[s].append(Pod(s, start, ready, max(ready, t_next), 0.0, True))
                    st.prewarm += 1
        elif spec.policy is not None and b + 1 <= ep.n_batches:
            last = spec.policy.decide(ep.context(b + 1))
            st.decisions += 1
            st.abstain += last.mode in ("prior", "none")
            per = merge_by_server(last.warm, cold)
            counts = {s: len(alive(s, d)) for s in per}
            total = sum(len(alive(s, d)) for s in list(pods))
            grant = apply_caps(per, counts, env.decide, cold, total_alive=total)
            for s, wd in per.items():
                target = d + wd.start_in_ms + env.cold_ms(s)
                for p in alive(s, d)[:wd.copies]:
                    if p.fixed_end is None:
                        p.expire = max(p.expire, max(p.ready, target) + wd.ttl_ms)
                for _ in range(grant.get(s, 0)):
                    start = d + wd.start_in_ms
                    ready = start + env.cold_ms(s)
                    pods[s].append(Pod(s, start, ready, ready + wd.ttl_ms, spec.reactive_ttl_ms,
                                       True))
                    st.prewarm += 1

    for s in list(pods):
        done.extend(pods[s])
    for p in done:
        end = p.fixed_end if p.fixed_end is not None else env.removal(p.expire)
        end = max(end, p.start)
        life = end - p.start
        startup = min(p.ready, end) - p.start
        st.pod_ms += life
        st.startup_ms += startup
        st.idle_ms += max(0.0, life - startup - p.busy_ms)
        cpu_s = life / 1000.0 * env.cpu(p.server)
        st.cpu_s += cpu_s
        if not p.used:
            st.unused_cpu_s += cpu_s
        if p.prewarm:
            st.prewarm_used += p.used
        if p.fixed_end is None:  # expire = last use (or last refresh) + TTL
            st.fr3_max_lag_ms = max(st.fr3_max_lag_ms, end - p.expire)
    if fr2_n:
        st.fr2_sessions += 1
        st.fr2_calls += fr2_n
        st.fr2_ready += fr2_ok
        st.fr2_all += fr2_ok == fr2_n
        st.fr2_80 += fr2_ok >= 0.8 * fr2_n


# --------------------------------------------------------------------------- models
class Precomputed(NextToolModel):
    """batch_probs looked up from a table filled once per decision point."""

    name = "precomputed"

    def __init__(self, table: dict[tuple[int, int], dict[str, float]]) -> None:
        self.table = table

    def fit(self, train: Sequence[Episode], val: Sequence[Episode]) -> Precomputed:
        return self

    def predict(self, ctx: Context, k: int = 1) -> dict[str, float]:
        raise NotImplementedError

    def batch_probs(self, ctx: Context) -> dict[str, float]:
        return self.table[(id(ctx.events), ctx.upto)]


class _Top1Ranked(NextToolModel):
    """Markov-1 next-tool distribution exposed through ``predict`` (ranked) only."""

    name = "markov1"

    def __init__(self, m: Markov) -> None:
        self.m = m

    def fit(self, train: Sequence[Episode], val: Sequence[Episode]) -> _Top1Ranked:
        return self

    def predict(self, ctx: Context, k: int = 1) -> dict[str, float]:
        return self.m.predict(ctx, k)


def specs_for(source_cfg: dict[str, Any], sim: dict[str, Any], ranker: LGBMRanker,
              markov: Markov, table: dict[tuple[int, int], dict[str, float]],
              env: Env) -> list[Spec]:
    cold, dcfg, rttl = env.cold, env.decide.with_(plan_mode=env.mode), float(
        sim["reactive_ttl_ms"])
    out = [Spec("scale_to_zero", 0.0), Spec("always_warm", rttl, always_warm=True)]
    out += [Spec(f"keep_alive_{k}", 1000.0 * k) for k in sim["keep_alive_s"]]
    mk = Decider(Ladder([("markov1", _Top1Ranked(markov))]), None, cold,
                 dcfg.with_(tau=float(sim["markov_tau"]), min_conf=0.0), top1=True,
                 timing=False, copies=False, prob="ranked")
    out.append(Spec("markov1_top1", rttl, float(sim["markov_tau"]), mk))
    lad = Ladder([("ranker", Precomputed(table))])
    g = ranker.base.graph if ranker.base is not None else None
    for tau in sim["tau_sweep"]:
        out.append(Spec(f"ours_tau{tau:.1f}", rttl, float(tau),
                        Decider(lad, g, cold, dcfg.with_(tau=float(tau)), env.pricing)))
        out.append(Spec(f"ours_now_tau{tau:.1f}", rttl, float(tau),
                        Decider(lad, g, cold, dcfg.with_(tau=float(tau)), env.pricing,
                                timing=False)))
    out.append(Spec("ours_cost", rttl, None,
                    Decider(lad, g, cold, dcfg.with_(threshold="cost"), env.pricing)))
    out.append(Spec("oracle", 0.0, oracle=True))
    return out


# --------------------------------------------------------------------------- runner
def _row(st: Stats, spec: Spec, base: dict[str, Any], price: float, purge_ms: float
         ) -> dict[str, Any]:
    k = 1000.0 / max(st.calls, 1)
    a = np.asarray(st.added) if st.added else np.zeros(1)

    def pct(n: int, d: int) -> str:
        return f"{100.0 * n / d:.4f}" if d else ""

    return {**base, "policy": spec.name, "tau": "" if spec.tau is None else spec.tau,
            "calls": st.calls, "batches": st.batches,
            "warm_pct": pct(st.warm, st.calls), "cold_pct": pct(st.calls - st.warm, st.calls),
            "batch_full_warm_pct": pct(st.batches_warm, st.batches),
            "added_ms_mean": f"{a.mean():.2f}", "added_ms_p50": f"{np.percentile(a, 50):.2f}",
            "added_ms_p95": f"{np.percentile(a, 95):.2f}",
            "idle_pod_s_per_1k": f"{st.idle_ms / 1000.0 * k:.3f}",
            "pod_s_per_1k": f"{st.pod_ms / 1000.0 * k:.3f}",
            "startup_pod_s_per_1k": f"{st.startup_ms / 1000.0 * k:.3f}",
            "cpu_s_per_1k": f"{st.cpu_s * k:.4f}",
            "cost_usd_per_1k": f"{st.cpu_s * k * price:.6g}",
            "unused_cpu_s": f"{st.unused_cpu_s:.3f}", "cpu_s": f"{st.cpu_s:.3f}",
            "hideable_pct": pct(st.hide_calls, st.calls),
            "cold_pct_hideable": pct(st.hide_cold, st.hide_calls),
            "cold_pct_nonhideable": pct(st.nohide_cold, st.nohide_calls),
            "prewarm_pods": st.prewarm, "prewarm_used_pct": pct(st.prewarm_used, st.prewarm),
            "abstain_pct": pct(st.abstain, st.decisions),
            "fr2_calls": st.fr2_calls, "fr2_call_ready_pct": pct(st.fr2_ready, st.fr2_calls),
            "fr2_sessions": st.fr2_sessions,
            "fr2_sessions_all_pct": pct(st.fr2_all, st.fr2_sessions),
            "fr2_sessions_80_pct": pct(st.fr2_80, st.fr2_sessions),
            "fr3_max_lag_ms": f"{st.fr3_max_lag_ms:.1f}",
            "fr3_pass": st.fr3_max_lag_ms <= purge_ms}


def _append(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        if new:
            w.writeheader()
        w.writerows(rows)


def hideable_rows(test: Sequence[Episode], env: Env, base: dict[str, Any]
                  ) -> list[dict[str, Any]]:
    """Share of calls whose think gap is at least their server's cold start, per class."""
    n: Counter[str] = Counter()
    h: Counter[str] = Counter()
    for ep in test:
        times = batch_times(ep)
        for b, (lo, hi) in enumerate(ep.batches):
            gap = times[b][0] - times[b - 1][1] if b > 0 else -math.inf
            for e in ep.events[lo:hi]:
                ok = gap >= env.cold_ms(env.cold.server(e.tool))
                for key in (e.tool, "ALL"):
                    n[key] += 1
                    h[key] += ok
    return [{**base, "tool_class": c, "server": "" if c == "ALL" else env.cold.server(c),
             "calls": n[c], "hideable_pct": f"{100.0 * h[c] / n[c]:.4f}"}
            for c in sorted(n, key=lambda c: (c != "ALL", c))]


def run_source(src: dict[str, Any], sim: dict[str, Any], cold: ColdStarts,
               pricing: dict[str, Any], dcfg: DecideConfig, csv_path: Path = CSV_PATH,
               hide_path: Path = HIDE_CSV, root: Path | None = None) -> list[dict[str, Any]]:
    source, vocab = src["source"], src.get("vocab")
    kw = {"vocab": vocab} if root is None else {"vocab": vocab, "root": root}
    train, val, test = split_groups(source, "fixed", **kw)
    test = [ep for ep in test if ep.has_timing]
    ranker = LGBMRanker.from_config().fit(train, val)
    markov = Markov(1).fit(train, val)
    ctxs = [ep.context(i) for ep in test for i in range(1, ep.n_batches + 1)]
    table = {(id(c.events), c.upto): p
             for c, p in zip(ctxs, ranker.batch_probs_many(ctxs), strict=True)}
    servers = sorted({cold.server(e.tool) for ep in train for e in ep.events})
    commit, now = git_commit(), datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    cfg = json.dumps({"sim": sim, "decide": dcfg.__dict__, "ranker_calibration":
                      ranker.calibration, "pricing_status": pricing.get("status")},
                     sort_keys=True, default=list)
    rows: list[dict[str, Any]] = []
    for mode in sim["cold_modes"]:
        env = Env(cold, mode, sim, pricing, dcfg)
        base = {"time": now, "source": source, "vocab": vocab or "", "cold_mode": mode,
                "sessions": len(test), "cold_start_label": cold.label,
                "cold_start_sha256": cold.sha256}
        _append(hide_path, [{**r, "git_commit": commit}
                            for r in hideable_rows(test, env, base)])
        for spec in specs_for(src, sim, ranker, markov, table, env):
            st = Stats()
            for ep in test:
                simulate(ep, spec, env, servers, st)
            rows.append(_row(st, spec, base, float(pricing["vcpu_second_usd"]),
                             float(sim["purge_ms"])))
        aw = next(r for r in rows if r["cold_mode"] == mode and r["policy"] == "always_warm")
        for r in rows:
            if r["cold_mode"] == mode:
                r["nfr3_unused_over_always_warm"] = (
                    f"{float(r['unused_cpu_s']) / float(aw['cpu_s']):.4f}"
                    if float(aw["cpu_s"]) else "")
                r["config"], r["git_commit"] = cfg, commit
                print(f"{source:13s} {mode:8s} {r['policy']:15s} "
                      f"warm {float(r['warm_pct']):6.2f}% "
                      f"cold(hide) {r['cold_pct_hideable'] or '-':>8s} "
                      f"idle/1k {float(r['idle_pod_s_per_1k']):10.1f} "
                      f"$/1k {float(r['cost_usd_per_1k']):.3g}", flush=True)
        _append(csv_path, [r for r in rows if r["cold_mode"] == mode])
    return rows


def load_config(path: Path = CONFIG) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--cold-start", default=None, help="cold-start YAML (default: sim.yaml)")
    p.add_argument("--sources", default="all")
    p.add_argument("--figures", action="store_true")
    args = p.parse_args(argv)
    sim = load_config()
    if args.figures:
        from sim.figures import main as figs
        return figs()
    path = Path(args.cold_start or sim["cold_start_path"])
    path = path if path.is_absolute() else ROOT / path
    cold = ColdStarts.load(path)
    print(f"cold starts: {cold.path} ({cold.label}, sha256 {cold.sha256})", flush=True)
    pricing, dcfg = load_pricing(), DecideConfig.load()
    want = None if args.sources == "all" else set(args.sources.split(","))
    for src in sim["sources"]:
        if want is None or src["source"] in want:
            run_source(src, sim, cold, pricing, dcfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
