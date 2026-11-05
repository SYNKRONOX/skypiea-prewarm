"""Offline trace simulation of the warm pool (Phase 6).

    nice -n 10 python -m sim.offline --cold-start PATH   # all sources in configs/model/sim.yaml
    python -m sim.offline --sources tau2_bench --cold-start PATH
    python -m sim.offline --figures                      # figures + markdown from the CSVs
    python -m sim.offline --runs KIND.yaml:0:none,AWS.yaml:2:parallel_3   # one fit, several runs

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
- Scale-ups (configs/model/sim.yaml; the controller's maxScaleUpsPerNode): at most
  ``max_concurrent_scale_ups`` pods of a session are starting at once; a further start waits
  for a slot (first come; no cross-session sharing, so each session is its own node). With
  ``contention: parallel_3`` a start that overlaps another takes the file's parallel_3 time
  (3 at once, measured), and so do the starts it joins at the same instant; a start already
  running is not lengthened by a later one. With cap 0 and contention none the results equal
  the model without these (checked on all test sessions).
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
import gzip
import io
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
from predictors.decide import (
    DecideConfig,
    Decider,
    apply_caps,
    load_pricing,
    merge_by_server,
    profile_of,
)
from predictors.eval import RESULTS, git_commit, split_groups
from predictors.ladder import Ladder
from predictors.lgbm_ranker import LGBMRanker

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "configs" / "model" / "sim.yaml"
CSV_PATH = RESULTS / "sim_offline.csv.gz"  # gzipped: the plain file is over 1 MB
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
    need_next: int = 0  # prewarm pods: calls of this server in the batch after the decision
    t_next: float = math.inf  # prewarm pods: that batch's arrival
    par: bool = False  # started while another scale-up was in progress (parallel_3 duration)


@dataclass
class Spec:
    """One policy run: how it reacts to cold calls and what it does at decision points."""

    name: str
    reactive_ttl_ms: float
    tau: float | None = None
    policy: ContextPolicy | None = None
    always_warm: bool = False
    oracle: bool = False
    # oracle_cap: the oracle plans its starts for the scale-up cap (see simulate)
    oracle_cap: bool = False
    # after a use: "pod" keeps the pod's own TTL (reactive_ttl_ms; the old rule), "contract"
    # applies max(ttl of the latest decision naming the server, reactive_ttl_ms) (P-new, with
    # reactive_ttl_ms = keep_after_use)
    after_use: str = "pod"


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
    calls_warm: list[bool] | None = None  # per-call outcome, in event order, if requested
    dec_servers: int = 0  # per-server warm decisions
    dec_already: int = 0  # ... whose server already had enough live pods (nothing started)
    dec_partial: int = 0  # ... that had some but not enough
    unused_wrong: int = 0  # prewarmed pods never used: server not in the next batch
    unused_surplus: int = 0  # ... server in the next batch, but other pods served it
    unused_expired: int = 0  # ... removed before the next batch arrived
    scaleups: int = 0  # pod starts (always-warm pods excluded)
    queued: int = 0  # ... that waited for a scale-up slot (max_concurrent_scale_ups)
    queue_ms: float = 0.0  # total slot wait
    contended: int = 0  # ... that overlapped another start (parallel_3 duration)


class Env:
    """Cold starts, prices and simulation parameters for one cold mode."""

    def __init__(self, cold: ColdStarts, mode: str, sim: dict[str, Any],
                 pricing: dict[str, Any], decide: DecideConfig) -> None:
        self.cold, self.mode, self.sim, self.pricing, self.decide = cold, mode, sim, pricing, decide
        self.tick = float(sim["tick_ms"])
        self.conc = int(sim["pod_concurrency"])
        self.stat = str(sim["cold_stat"])
        self.cap = int(sim.get("max_concurrent_scale_ups", 0))  # 0 = no limit
        self.contention = str(sim.get("contention", "none"))  # none | parallel_3
        if self.contention not in ("none", "parallel_3"):
            raise ValueError(f"contention must be none or parallel_3, not {self.contention!r}")
        self._cpu = {s: float(pricing["cpu_request_cores"][cold.server_tier(s)])
                     for s in cold.servers}

    def cold_ms(self, server: str) -> float:
        return self.cold.server_ms(server, self.mode, self.stat)

    def start_ms(self, server: str, overlapped: bool) -> float:
        """A pod start's duration: the parallel_3 value if it overlaps another start."""
        if overlapped and self.contention == "parallel_3":
            v = self.cold.parallel_ms(server, self.mode, self.stat)
            if v is not None:
                return max(v, self.cold_ms(server))
        return self.cold_ms(server)

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

    starting: list[Pod] = []  # pods with a scale-up that may still be in progress

    def launch(pod: Pod, t_req: float, now: float) -> None:
        """Start ``pod`` at the first time >= t_req with a free scale-up slot. A start that
        overlaps another takes the parallel_3 duration, and so do the starts it joins at the
        same instant (their effects are not final yet). Keeps expire - ready."""
        nonlocal starting
        starting = [p for p in starting if p.ready > now and p is not pod]

        def busy(x: float) -> int:
            return sum(1 for p in starting if p.start <= x < p.ready)

        start = t_req
        if env.cap > 0:
            for c in sorted({t_req, *(p.ready for p in starting if p.ready > t_req)}):
                if busy(c) < env.cap:
                    start = c
                    break
        over = busy(start) > 0
        off = pod.expire - pod.ready
        pod.start, pod.ready, pod.par = start, start + env.start_ms(pod.server, over), over
        pod.expire = pod.ready + off
        if over:
            for p in starting:
                if p.start == start and not p.par:
                    nr = start + env.start_ms(p.server, True)
                    p.expire += nr - p.ready
                    p.ready, p.par = nr, True
                    st.contended += 1
        starting.append(pod)
        st.scaleups += 1
        st.queued += start > t_req
        st.queue_ms += start - t_req
        st.contended += over

    last: Decision | None = None
    dec_ttl: dict[str, float] = {}  # ttl of the latest decision naming each server
    fr2_n = fr2_ok = 0
    for b, (lo, hi) in enumerate(ep.batches):
        t, _ = times[b]
        gap = t - times[b - 1][1] if b > 0 else -math.inf
        all_warm = True
        # waits are settled after the whole batch: a later start in the same instant can
        # lengthen an earlier one (contention)
        pending: list[tuple[Any, str, Pod, float, bool]] = []
        for e in ep.events[lo:hi]:
            s = cold.server(e.tool)
            live = alive(s, t)
            free = [p for p in live if p.busy_until <= t and p.start <= t]
            ready = [p for p in free if p.ready <= t]
            svc = float(e.service_ms or 0.0)
            if ready:
                pod, known = min(ready, key=lambda p: p.ready), True
            elif free:
                pod, known = min(free, key=lambda p: p.ready), False
            else:
                later = [p for p in live if p.start > t]
                if later:  # scheduled (or queued) for later: start it now, same expiry
                    pod = min(later, key=lambda p: p.start)
                    keep = pod.expire
                    launch(pod, t, t)
                    pod.expire = max(keep, pod.ready)
                else:
                    pod = Pod(s, t, t, t, spec.reactive_ttl_ms, False)
                    pods[s].append(pod)
                    launch(pod, t, t)
                known = False
            pod.busy_until = (t + svc if env.conc == 1 else t) if known else math.inf
            pending.append((e, s, pod, svc, known))
        for e, s, pod, svc, known in pending:
            cms = env.cold_ms(s)
            wait = 0.0 if known else max(0.0, pod.ready - t)
            serve = t + wait
            pod.busy_until = serve + svc if env.conc == 1 else serve
            pod.busy_ms += svc
            pod.used, pod.last_use = True, serve + svc
            if pod.fixed_end is None:
                ttl = (max(spec.reactive_ttl_ms, dec_ttl.get(s, 0.0))
                       if spec.after_use == "contract" else pod.ttl)
                pod.expire = max(pod.expire, serve + svc + ttl)
            warm = wait <= 0.0
            st.calls += 1
            st.warm += warm
            if st.calls_warm is not None:
                st.calls_warm.append(warm)
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
            new = {s: n - len([p for p in alive(s, d)
                               if p.expire >= t_next and p.busy_until <= t_next])
                   for s, n in sorted(need.items())}
            new = {s: n for s, n in new.items() if n > 0}
            lead = None
            if spec.oracle_cap and env.cap > 0 and new:
                # all the batch's starts in ceil(n / cap) waves, each as long as its slowest
                # start (the parallel_3 time when a wave holds more than one): request every
                # start that long before the batch and let the cap queue them
                n_new = sum(new.values())
                lead = math.ceil(n_new / env.cap) * max(
                    env.start_ms(s, min(n_new, env.cap) > 1) for s in new)
            for s, n in new.items():
                for _ in range(n):
                    pod = Pod(s, d, d, d, 0.0, True)
                    pods[s].append(pod)
                    launch(pod, max(d, t_next - (env.cold_ms(s) if lead is None else lead)), d)
                    pod.expire = max(pod.ready, t_next)
                    st.prewarm += 1
        elif spec.policy is not None and b + 1 <= ep.n_batches:
            last = spec.policy.decide(ep.context(b + 1))
            st.decisions += 1
            st.abstain += last.mode in ("prior", "none")
            per = merge_by_server(last.warm, cold)
            counts = {s: len(alive(s, d)) for s in per}
            total = sum(len(alive(s, d)) for s in list(pods))
            grant = apply_caps(per, counts, env.decide, cold, total_alive=total)
            nxt = (Counter(cold.server(x) for x in ep.batch_tools(b + 1))
                   if b + 1 < ep.n_batches else Counter())
            t_nx = times[b + 1][0] if b + 1 < ep.n_batches else math.inf
            for s, wd in per.items():
                dec_ttl[s] = wd.ttl_ms
                want = min(wd.copies, env.decide.max_pods_per_tool)
                st.dec_servers += 1
                st.dec_already += counts[s] >= want
                st.dec_partial += 0 < counts[s] < want
                target = d + wd.start_in_ms + env.cold_ms(s)
                for p in alive(s, d)[:wd.copies]:
                    if p.fixed_end is None:
                        p.expire = max(p.expire, max(p.ready, target) + wd.ttl_ms)
                for _ in range(grant.get(s, 0)):
                    pod = Pod(s, d, d, d + wd.ttl_ms, spec.reactive_ttl_ms, True,
                              need_next=nxt[s], t_next=t_nx)
                    pods[s].append(pod)
                    launch(pod, d + wd.start_in_ms, d)
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
            if not p.used and not spec.oracle:
                if p.need_next == 0:
                    st.unused_wrong += 1
                elif p.expire < p.t_next:
                    st.unused_expired += 1
                else:
                    st.unused_surplus += 1
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


@dataclass
class Prepared:
    """One source's replayed split with the models fitted on train (and val, see LGBMRanker)."""

    source: str
    vocab: str | None
    split: str
    eps: list[Episode]
    ranker: LGBMRanker
    markov: Markov
    table: dict[tuple[int, int], dict[str, float]]
    servers: list[str]


_PREPARED: dict[tuple[Any, ...], Prepared] = {}  # one fit per source within a process


def prepare(src: dict[str, Any], cold: ColdStarts, split: str = "test",
            root: Path | None = None) -> Prepared:
    key = (src["source"], src.get("vocab"), split, str(root),
           tuple(sorted(cold.class_server.items())), cold.fallback)
    if key not in _PREPARED:
        _PREPARED[key] = _prepare(src, cold, split, root)
    return _PREPARED[key]


def _prepare(src: dict[str, Any], cold: ColdStarts, split: str, root: Path | None
             ) -> Prepared:
    source, vocab = src["source"], src.get("vocab")
    kw = {"vocab": vocab} if root is None else {"vocab": vocab, "root": root}
    train, val, test = split_groups(source, "fixed", **kw)
    eps = [ep for ep in {"val": val, "test": test}[split] if ep.has_timing]
    ranker = LGBMRanker.from_config().fit(train, val)
    markov = Markov(1).fit(train, val)
    ctxs = [ep.context(i) for ep in eps for i in range(1, ep.n_batches + 1)]
    table = {(id(c.events), c.upto): p
             for c, p in zip(ctxs, ranker.batch_probs_many(ctxs), strict=True)}
    servers = sorted({cold.server(e.tool) for ep in train for e in ep.events})
    return Prepared(source, vocab, split, eps, ranker, markov, table, servers)


def baseline_specs(sim: dict[str, Any], prep: Prepared, env: Env) -> list[Spec]:
    rttl = float(sim["reactive_ttl_ms"])
    out = [Spec("scale_to_zero", 0.0), Spec("always_warm", rttl, always_warm=True)]
    out += [Spec(f"keep_alive_{k}", 1000.0 * k) for k in sim["keep_alive_s"]]
    mk = Decider(Ladder([("markov1", _Top1Ranked(prep.markov))]), None, env.cold,
                 env.decide.with_(plan_mode=env.mode, threshold="fixed",
                                  tau=float(sim["markov_tau"]),
                                  min_conf=0.0, keep_after_use_s=rttl / 1000.0),
                 top1=True, timing=False, copies=False, prob="ranked")
    out.append(Spec("markov1_top1", rttl, float(sim["markov_tau"]), mk))
    return out


def ours_spec(name: str, prep: Prepared, env: Env, cfg: DecideConfig, after_use: str = "contract"
              ) -> Spec:
    """Our policy with decision config ``cfg`` (keep-after-use = cfg.keep_after_use_s)."""
    cfg = cfg.with_(plan_mode=env.mode)
    g = prep.ranker.base.graph if prep.ranker.base is not None else None
    pol = Decider(Ladder([("ranker", Precomputed(prep.table))]), g, env.cold, cfg, env.pricing)
    return Spec(name, 1000.0 * cfg.keep_after_use_s,
                cfg.tau if cfg.threshold == "fixed" else None, pol, after_use=after_use)


OLD = {"start_rule": "timing", "gap_stat": "p50", "plan_stat": "p95_ms", "copies_quantile": 0.9,
       "max_copies_per_tool": 3, "keep_after_use_s": 30.0, "threshold": "fixed"}


def specs_for(sim: dict[str, Any], prep: Prepared, env: Env) -> list[Spec]:
    """Baselines, our old policy (Phase 6 first pass), our shipped policy (decide.yaml) over
    the tau and lambda sweeps plus its default, and the oracle."""
    out = baseline_specs(sim, prep, env)
    old = env.decide.with_(**OLD)
    for tau in sim["tau_sweep"]:
        out.append(ours_spec(f"ours_old_tau{tau:.1f}", prep, env, old.with_(tau=float(tau)),
                             after_use="pod"))
        out.append(ours_spec(f"ours_old_now_tau{tau:.1f}", prep, env,
                             old.with_(tau=float(tau), start_rule="now"), after_use="pod"))
        out.append(ours_spec(f"ours_new_tau{tau:.1f}", prep, env,
                             env.decide.with_(threshold="fixed", tau=float(tau))))
    for lam in sim["lambda_sweep"]:
        out.append(ours_spec(f"ours_new_lam{lam:g}", prep, env,
                             env.decide.with_(threshold="cost", lambda_pod_s=float(lam))))
    out.append(ours_spec("ours_new", prep, env, env.decide))
    out.append(Spec("oracle", 0.0, oracle=True))
    if env.cap > 0:
        out.append(Spec("oracle_cap", 0.0, oracle=True, oracle_cap=True))
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
            "dec_servers": st.dec_servers,
            "dec_already_warm_pct": pct(st.dec_already, st.dec_servers),
            "dec_partial_pct": pct(st.dec_partial, st.dec_servers),
            "unused_wrong_server_pct": pct(st.unused_wrong, st.prewarm),
            "unused_surplus_copy_pct": pct(st.unused_surplus, st.prewarm),
            "unused_expired_pct": pct(st.unused_expired, st.prewarm),
            "idle_over_always_warm": "",
            "fr2_calls": st.fr2_calls, "fr2_call_ready_pct": pct(st.fr2_ready, st.fr2_calls),
            "fr2_sessions": st.fr2_sessions,
            "fr2_sessions_all_pct": pct(st.fr2_all, st.fr2_sessions),
            "fr2_sessions_80_pct": pct(st.fr2_80, st.fr2_sessions),
            "fr3_max_lag_ms": f"{st.fr3_max_lag_ms:.1f}",
            "fr3_pass": st.fr3_max_lag_ms <= purge_ms,
            "scaleups": st.scaleups, "scaleup_queued_pct": pct(st.queued, st.scaleups),
            "scaleup_queue_ms_mean": f"{st.queue_ms / max(st.queued, 1):.1f}",
            "scaleup_contended_pct": pct(st.contended, st.scaleups),
            "added_ms_cdf": json.dumps([round(float(v), 1)
                                        for v in np.percentile(a, np.arange(0, 101, 2))])}


def _open(path: Path, mode: str) -> io.TextIOBase:
    """Text file; ``.gz`` paths are gzip with no name and mtime 0: same rows, same bytes."""
    if path.suffix == ".gz":
        raw = path.open(mode + "b")
        gz = gzip.GzipFile(filename="", mode=mode + "b", fileobj=raw, mtime=0)
        gz.myfileobj = raw  # closed with the gzip stream
        return io.TextIOWrapper(gz, newline="")
    return path.open(mode, newline="")


def _append(path: Path, rows: list[dict[str, Any]]) -> None:
    """Append rows (``.csv`` or ``.csv.gz``); the file is rewritten with the union of columns."""
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = list(dict.fromkeys(k for r in rows for k in r))
    old: list[dict[str, str]] = []
    if path.exists():
        with _open(path, "r") as f:
            r = csv.DictReader(f)
            have = list(r.fieldnames or [])
            old = list(r)
        cols = have + [c for c in cols if c not in have]
    with _open(path, "w") as f:
        w = csv.DictWriter(f, fieldnames=cols, restval="")
        w.writeheader()
        w.writerows(old)
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


def run_specs(prep: Prepared, specs: Sequence[Spec], env: Env, sim: dict[str, Any],
              extra: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """One row per spec; NFR-3 ratios are filled in against the run's always_warm row."""
    base = {"time": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "split": prep.split,
            "source": prep.source, "vocab": prep.vocab or "", "cold_mode": env.mode,
            "sessions": len(prep.eps), "cold_start_label": env.cold.label,
            "cold_start_sha256": env.cold.sha256, **(extra or {})}
    rows = []
    for spec in specs:
        st = Stats()
        for ep in prep.eps:
            simulate(ep, spec, env, prep.servers, st)
        rows.append(_row(st, spec, base, float(env.pricing["vcpu_second_usd"]),
                         float(sim["purge_ms"])))
        dec = spec.policy.cfg.__dict__ if isinstance(spec.policy, Decider) else None
        rows[-1]["decide"] = json.dumps(dec, sort_keys=True, default=list) if dec else ""
        rows[-1]["after_use"] = spec.after_use if dec else ""
    return rows


def nfr3(rows: list[dict[str, Any]], aw: dict[str, Any]) -> None:
    for r in rows:
        r["nfr3_unused_over_always_warm"] = (
            f"{float(r['unused_cpu_s']) / float(aw['cpu_s']):.4f}" if float(aw["cpu_s"]) else "")
        a = float(aw["idle_pod_s_per_1k"])
        r["idle_over_always_warm"] = f"{float(r['idle_pod_s_per_1k']) / a:.4f}" if a else ""


def run_source(src: dict[str, Any], sim: dict[str, Any], cold: ColdStarts,
               pricing: dict[str, Any], dcfg: DecideConfig, csv_path: Path = CSV_PATH,
               hide_path: Path = HIDE_CSV, root: Path | None = None) -> list[dict[str, Any]]:
    prep = prepare(src, cold, "test", root)
    profile = profile_of(prep.source)
    if profile is not None:  # the source's profile is "ours_new" (configs/model/profiles.yaml)
        dcfg = DecideConfig.for_profile(profile)
    commit = git_commit()
    cfg = json.dumps({"sim": sim, "decide": dcfg.__dict__, "ranker_calibration":
                      prep.ranker.calibration, "pricing_status": pricing.get("status")},
                     sort_keys=True, default=list)
    out: list[dict[str, Any]] = []
    for mode in sim["cold_modes"]:
        env = Env(cold, mode, sim, pricing, dcfg)
        base = {"time": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "source": prep.source, "vocab": prep.vocab or "", "cold_mode": mode,
                "sessions": len(prep.eps), "cold_start_label": cold.label,
                "cold_start_sha256": cold.sha256, "scaleup_cap": env.cap,
                "contention": env.contention}
        _append(hide_path, [{**r, "git_commit": commit}
                            for r in hideable_rows(prep.eps, env, base)])
        rows = run_specs(prep, specs_for(sim, prep, env), env, sim,
                         {"profile": profile or "", "scaleup_cap": env.cap,
                          "contention": env.contention})
        nfr3(rows, next(r for r in rows if r["policy"] == "always_warm"))
        for r in rows:
            r["config"], r["git_commit"] = cfg, commit
            print(f"{prep.source:13s} {mode:8s} {r['policy']:18s} "
                  f"warm {float(r['warm_pct']):6.2f}% "
                  f"idle/1k {float(r['idle_pod_s_per_1k']):10.1f} "
                  f"used {r['prewarm_used_pct'] or '-':>8s}", flush=True)
        _append(csv_path, rows)
        out += rows
    return out


def load_config(path: Path = CONFIG) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--cold-start", default=None, help="cold-start YAML (default: sim.yaml)")
    p.add_argument("--runs", default=None,
                   help="several runs in one process (one ranker fit per source): "
                        "PATH:CAP:CONTENTION,... (overrides the three options below)")
    p.add_argument("--sources", default="all")
    p.add_argument("--figures", action="store_true")
    p.add_argument("--scaleup-cap", type=int, default=None,
                   help="max concurrent scale-ups per session's node (0 = none; sim.yaml)")
    p.add_argument("--contention", choices=["none", "parallel_3"], default=None)
    p.add_argument("--out", default=None, help="results CSV (default results/sim_offline.csv.gz)")
    args = p.parse_args(argv)
    sim = load_config()
    if args.scaleup_cap is not None:
        sim["max_concurrent_scale_ups"] = args.scaleup_cap
    if args.contention is not None:
        sim["contention"] = args.contention
    out = Path(args.out) if args.out else CSV_PATH
    hide = out.with_name(out.name.replace("offline", "hideable")) if args.out else HIDE_CSV
    if args.figures:
        from sim.figures import main as figs
        return figs()
    if args.runs:
        runs = [(a, int(b), c) for a, b, c in (r.rsplit(":", 2) for r in args.runs.split(","))]
    else:
        runs = [(args.cold_start or sim["cold_start_path"],
                 int(sim.get("max_concurrent_scale_ups", 0)), str(sim.get("contention", "none")))]
    pricing, dcfg = load_pricing(), DecideConfig.load()
    want = None if args.sources == "all" else set(args.sources.split(","))
    for src in sim["sources"]:
        if want is not None and src["source"] not in want:
            continue
        for path_s, cap, contention in runs:
            path = Path(path_s).expanduser()
            path = path if path.is_absolute() else ROOT / path
            cold = ColdStarts.load(path)
            print(f"cold starts: {cold.path} ({cold.label}, sha256 {cold.sha256}); "
                  f"scale-up cap {cap}, contention {contention}", flush=True)
            run_source(src, {**sim, "max_concurrent_scale_ups": cap, "contention": contention},
                       cold, pricing, dcfg, out, hide)
    return 0


if __name__ == "__main__":
    sys.exit(main())
