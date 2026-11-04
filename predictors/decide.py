"""Warm decisions from next-tool probabilities (Phase 5).

    cfg = DecideConfig.load()                         # configs/model/decide.yaml
    cold = ColdStarts.load(path)                      # the infra cold-start file
    pol = Decider(ladder, graph, cold, cfg, pricing)
    d = pol.decide(ctx)                               # Decision(warm={class: WarmDecision}, mode)
    per_server = merge_by_server(d.warm, cold)        # P7: copies max, start min, ttl max
    grant = apply_caps(per_server, alive, cfg, cold)  # operator caps, ramp limit

For each tool class j with in-next-batch probability p_j (the ladder's first healthy rung):

- abstain (``mode="prior"``, no decisions) if the last tool was never seen in train or
  max_j p_j < ``min_conf``; every rung failing gives ``mode="none"``. Both mean the normal
  cold path (FR-4).
- threshold: fixed ``tau``, or cost-based (``threshold: cost``) with the knob ``lambda_pod_s``
  = pod-seconds worth paying to save one second of cold start. Warming j when it is not
  called wastes the pod's startup plus its hold (cold_s + ttl_s pod-seconds); warming it when
  it is called saves lambda x cold_s. Warm iff p_j >= tau_j = (cold_s + ttl_s) / (cold_s +
  ttl_s + lambda x cold_s), clamped to ``cost_tau_clamp``. cold_s is the cold-start file's
  median for ``plan_mode``; ttl_s the decision's TTL.
- copies: smallest n with P(count <= n | j in the batch) >= ``copies_quantile`` (the graph's
  context-conditioned table when it has support), capped at min(``max_copies_per_tool``, 10).
- start rule (``start_rule``): ``timing`` (the spec rule): gaps are the graph's think-gap
  quantiles for (last tool -> j), measured from the end of the previous batch, i.e. from the
  request. start_in_ms = max(0, gap_<gap_stat> - cold_plan - margin); the TTL runs from the
  moment the pod is Ready and lasts until the p90 arrival: ttl_ms = clamp(gap_p90 -
  start_in_ms - cold_plan, ttl_min, ttl_max), or ``ttl_default_ms`` without timing. cold_plan
  is the cold-start file's ``plan_stat`` for ``plan_mode``. ``now``: start_in 0, TTL
  ``ttl_default_ms``.
- ``keep_after_use_s`` is sent with every decision (contract P-new): after a pod serves a
  call the controller keeps it for max(ttl_s of the latest decision naming its server,
  keep_after_use_s).

``top1=True`` with ``timing=False, copies=False`` gives the SpecBox-style baseline (warm the
single most likely tool now, one copy, default TTL).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any

import yaml

from predictors.base import ContextPolicy, Decision, WarmDecision
from predictors.cold_start import ColdStarts
from predictors.data import END, Context
from predictors.graph import TransitionGraph
from predictors.ladder import Ladder
from predictors.workflow import START

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "configs" / "model" / "decide.yaml"
PRICING = ROOT / "configs" / "model" / "pricing.yaml"
PROFILES = ROOT / "configs" / "model" / "profiles.yaml"
CONTRACT_MAX_COPIES = 10


@dataclass(frozen=True)
class DecideConfig:
    threshold: str = "fixed"
    tau: float = 0.6
    lambda_pod_s: float = 10.0
    cost_tau_clamp: tuple[float, float] = (0.05, 0.99)
    min_conf: float = 0.3
    copies_quantile: float = 0.9
    max_copies_per_tool: int = 3
    ttl_default_ms: float = 30_000.0
    ttl_min_ms: float = 5_000.0
    ttl_max_ms: float = 600_000.0
    margin_ms: float = 0.0
    start_rule: str = "timing"
    gap_stat: str = "p50"
    keep_after_use_s: float = 30.0
    plan_stat: str = "p95_ms"
    plan_mode: str = "cached"
    max_pods_per_tool: int = 3
    max_total_warm_pods: int = 20
    max_new_pods_per_s: float = 5.0
    budget_ms: float = 40.0

    @classmethod
    def load(cls, path: Path = CONFIG, **overrides: Any) -> DecideConfig:
        d = {**yaml.safe_load(path.read_text(encoding="utf-8")), **overrides}
        names = {f.name for f in fields(cls)}
        unknown = set(d) - names
        if unknown:
            raise ValueError(f"unknown decide config keys: {sorted(unknown)}")
        if "cost_tau_clamp" in d:
            d["cost_tau_clamp"] = tuple(d["cost_tau_clamp"])
        cfg = cls(**d)
        if cfg.threshold not in ("fixed", "cost"):
            raise ValueError("threshold must be fixed or cost")
        if cfg.start_rule not in ("timing", "now") or cfg.gap_stat not in ("p10", "p50"):
            raise ValueError("start_rule must be timing or now; gap_stat p10 or p50")
        return cfg

    def with_(self, **kw: Any) -> DecideConfig:
        return replace(self, **kw)

    @classmethod
    def for_profile(cls, name: str, path: Path = CONFIG, profiles: Path = PROFILES
                    ) -> DecideConfig:
        """decide.yaml with the named profile's overrides (configs/model/profiles.yaml)."""
        prof = load_profiles(profiles)
        if name not in prof:
            raise ValueError(f"unknown profile {name!r}")
        return cls.load(path, **(prof[name].get("decide") or {}))


def load_profiles(path: Path = PROFILES) -> dict[str, dict[str, Any]]:
    return dict(yaml.safe_load(path.read_text(encoding="utf-8"))["profiles"])


def profile_of(source: str, path: Path = PROFILES) -> str | None:
    for name, p in load_profiles(path).items():
        if source in p.get("sources", []):
            return name
    return None


def load_pricing(path: Path = PRICING) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _clamp(x: float, lo: float, hi: float) -> float:
    return min(hi, max(lo, x))


def last_batch_tool(ctx: Context) -> str:
    """The tool the graph's timing tables key on: the last batch's tool with the largest name
    (as in ``predictors.graph._think_gaps``); START before the first call."""
    pre = ctx.prefix()
    if not pre:
        return START
    bid = pre[-1].batch_id
    tools = []
    for e in reversed(pre):
        if e.batch_id != bid:
            break
        tools.append(e.tool)
    return max(tools)


class Decider(ContextPolicy):
    def __init__(self, ladder: Ladder, graph: TransitionGraph | None, cold: ColdStarts,
                 cfg: DecideConfig, pricing: dict[str, Any] | None = None, *,
                 top1: bool = False, timing: bool = True, copies: bool = True,
                 prob: str = "batch") -> None:
        self.ladder, self.graph, self.cold, self.cfg = ladder, graph, cold, cfg
        self.pricing = pricing
        self.top1, self.use_timing, self.use_copies, self.prob = top1, timing, copies, prob

    def decide(self, ctx: Context) -> Decision:
        if self.prob == "batch":
            probs, mode, degraded = self.ladder.batch_probs(ctx)
        else:
            probs, mode, degraded = self.ladder.ranked(ctx)
        if mode == "none":
            return Decision({}, "none", True)
        tools = {t: float(p) for t, p in probs.items() if t != END and p > 0}
        if not tools or max(tools.values()) < self.cfg.min_conf or self._unseen(ctx):
            return Decision({}, "prior", degraded)
        if self.top1:
            best = min(tools, key=lambda t: (-tools[t], t))
            tools = {best: tools[best]}
        out: dict[str, WarmDecision] = {}
        for t in sorted(tools):
            p = tools[t]
            start_in, ttl = self.timing(ctx, t)
            if p < self.tau(t, ttl):
                continue
            out[t] = WarmDecision(copies=self.copies(ctx, t), start_in_ms=start_in,
                                  prob=p, ttl_ms=ttl)
        return Decision(out, mode, degraded)

    def _unseen(self, ctx: Context) -> bool:
        if self.graph is None or ctx.length == 0:
            return False
        return ctx.last(1)[0].tool not in self.graph.vocab

    def cold_plan(self, tool: str) -> float:
        return self.cold.ms(tool, self.cfg.plan_mode, self.cfg.plan_stat)

    def timing(self, ctx: Context, tool: str) -> tuple[float, float]:
        """(start_in_ms, ttl_ms)."""
        c = self.cfg
        if not self.use_timing or c.start_rule == "now" or self.graph is None:
            return 0.0, c.ttl_default_ms
        tm = self.graph.timing(last_batch_tool(ctx), tool)
        gap = tm.p10 if c.gap_stat == "p10" else tm.p50
        if gap is None or tm.p90 is None:
            return 0.0, c.ttl_default_ms
        cold = self.cold_plan(tool)
        start_in = max(0.0, gap - cold - c.margin_ms)
        ttl = _clamp(tm.p90 - start_in - cold, c.ttl_min_ms, c.ttl_max_ms)
        return float(round(start_in)), float(math.ceil(ttl))

    def tau(self, tool: str, ttl_ms: float) -> float:
        c = self.cfg
        if c.threshold == "fixed":
            return c.tau
        cold_s = self.cold.ms(tool, c.plan_mode, "median_ms") / 1000.0
        waste = cold_s + ttl_ms / 1000.0
        gain = c.lambda_pod_s * cold_s
        t = waste / (waste + gain) if waste + gain > 0 else c.cost_tau_clamp[1]
        return _clamp(t, *c.cost_tau_clamp)

    def copies(self, ctx: Context, tool: str) -> int:
        cap = min(self.cfg.max_copies_per_tool, CONTRACT_MAX_COPIES)
        if not self.use_copies or self.graph is None:
            return min(1, cap)
        dist = self.graph.copies(tool, ctx)
        cum = 0.0
        for n in sorted(dist):
            cum += dist[n]
            if cum >= self.cfg.copies_quantile - 1e-12:
                return max(1, min(n, cap))
        return max(1, min(max(dist), cap))


def merge_by_server(warm: dict[str, WarmDecision], cold: ColdStarts) -> dict[str, WarmDecision]:
    """Per-server decisions (contract P7): copies max, start_in min, ttl max, prob max."""
    out: dict[str, WarmDecision] = {}
    for t in sorted(warm):
        d, s = warm[t], cold.server(t)
        o = out.get(s)
        out[s] = d if o is None else WarmDecision(
            copies=max(o.copies, d.copies), start_in_ms=min(o.start_in_ms, d.start_in_ms),
            prob=max(o.prob, d.prob), ttl_ms=max(o.ttl_ms, d.ttl_ms))
    return out


def apply_caps(per_server: dict[str, WarmDecision], alive: dict[str, int], cfg: DecideConfig,
               cold: ColdStarts, total_alive: int | None = None,
               new_pod_budget: int | None = None) -> dict[str, int]:
    """New pods to start per server under the operator caps.

    ``alive`` counts live pods per server (ready or starting). Per server at most
    ``max_pods_per_tool`` live pods; overall at most ``max_total_warm_pods``; at most
    ``new_pod_budget`` new pods in this step (the ramp limit, if the caller tracks it). When a
    cap binds, pods go first to the highest prob x cold-start cost (cold-start ms).
    """
    want: list[tuple[float, str]] = []
    for s, d in per_server.items():
        need = min(d.copies, cfg.max_pods_per_tool) - alive.get(s, 0)
        want += [(-d.prob * cold.server_ms(s, cfg.plan_mode, "median_ms"), s)] * max(0, need)
    want.sort()
    room = cfg.max_total_warm_pods - (sum(alive.values()) if total_alive is None
                                      else total_alive)
    if new_pod_budget is not None:
        room = min(room, new_pod_budget)
    grant: dict[str, int] = {}
    for _, s in want[:max(0, room)]:
        grant[s] = grant.get(s, 0) + 1
    return grant
