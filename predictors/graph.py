"""Markov transition graph and its features (Phase 3), built from TRAIN data only.

    g = TransitionGraph().fit(train_episodes)
    g.one_step(ctx)            # P(next tool), with back-off; includes <end>
    g.k_step(ctx, 3)           # P(tool appears within the next 3 batches)
    g.timing("read_file", "write_file")   # think gap quantiles (ms), with fallback
    g.batch_next(ctx)          # distribution over the next batch's tool SET
    g.copies("read_file", ctx) # P(n calls of the tool | it is in the next batch)
    g.p_end(ctx)
    g.fleet_demand([ctx, ...]) # expected calls per tool at the next step, summed
    g.save("counts.json"); TransitionGraph.load("counts.json")

Contexts are the last n tools of the history (n = 3..0, padded with ``<start>``); the longest
context with total support >= ``min_support`` is used. If the workflow node is known and seen,
the node-conditioned table is tried first. Unseen contexts back off; nothing raises.

Poisoning guards (also applied when rebuilding from logged traces):
- each (context -> target) pair counts at most ``per_session_cap`` (3) times per session;
- pairs with support below ``min_support`` (3) are dropped;
- think gaps are clipped to [0, ``max_gap_ms``] (1 hour), then winsorised at the train
  p1/p99 before per-pair quantiles;
- sessions with more than ``max_events`` (2,000) events or non-finite / missing times
  (when the session claims timing) are dropped and counted.

The per-session cap bounds what ONE session can do, not what many can: thousands of junk
sessions still add up. ``rebuild()`` (used when re-training from logged traces) therefore
also down-weights the logged sessions so that together they count as at most
``max_new_share`` of the base training sessions, and accepts the rebuilt graph only if
top-1 accuracy on a clean validation set drops by at most ``max_val_drop``; otherwise the
base graph is kept and the reason reported.

All tables are plain dicts filled in ``fit`` or ``load`` and never written afterwards, so one
instance can be shared by concurrent requests. Serialisation is JSON (no pickle).
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from predictors.base import NextToolModel
from predictors.data import END, Context, Episode
from predictors.workflow import START

SCHEMA_VERSION = 1
CONFIG = Path(__file__).resolve().parent.parent / "configs" / "model" / "graph.yaml"
MAX_ORDER = 3
HORIZONS = (1, 2, 3, 4, 5)
WITHIN = (2, 3, 5)
SEP = "|"  # context separator; tool names never contain it (^[a-z0-9_]+$ or <start>/<end>)
SET_SEP = ","


@dataclass(frozen=True)
class Timing:
    p10: float | None
    p50: float | None
    p90: float | None
    n: int
    level: str  # "pair", "next", "global" or "none"


def _key(tools: Sequence[str]) -> str:
    return SEP.join(tools)


def context_keys(ctx: Context, max_order: int = MAX_ORDER) -> list[str]:
    """Context keys for orders 0..max_order (index = order)."""
    tools = ctx.tools(max_order)
    out = []
    for n in range(max_order + 1):
        if n == 0:
            out.append("")
            continue
        last = tools[len(tools) - n:] if len(tools) >= n else tools
        out.append(_key((START,) * (n - len(last)) + tuple(last)))
    return out


def _set_key(tools: Iterable[str]) -> str:
    return SET_SEP.join(sorted(set(tools)))


def data_hash(episodes: Sequence[Episode]) -> str:
    """sha256 of a canonical serialisation of the episodes (tools, times, errors)."""
    h = hashlib.sha256()
    for ep in sorted(episodes, key=lambda e: e.session_id):
        h.update(ep.session_id.encode())
        for e in ep.events:
            h.update(f"{e.tool}\x1f{e.t_offset_ms}\x1f{e.batch_id}\x1f{e.service_ms}"
                     f"\x1f{int(e.error)}\x1e".encode())
    return h.hexdigest()


def sanitize(episodes: Sequence[Episode], max_events: int = 2000) -> tuple[list[Episode],
                                                                            Counter[str]]:
    """Drop sessions that are too long or have broken times; count what was dropped."""
    kept, stats = [], Counter[str]()
    for ep in episodes:
        stats["sessions_in"] += 1
        if len(ep.events) > max_events:
            stats["dropped_too_many_events"] += 1
            continue
        if ep.has_timing and any(e.t_offset_ms is None or not math.isfinite(e.t_offset_ms)
                                 for e in ep.events):
            stats["dropped_non_finite_time"] += 1
            continue
        kept.append(ep)
    stats["sessions_kept"] = len(kept)
    return kept, stats


def _think_gaps(ep: Episode) -> list[tuple[str, str, float]]:
    """(previous last tool, next tool, gap ms) for each non-empty batch with timing.

    The gap runs from the end of the previous batch (start + slowest service time) to the
    next batch's start; for the first batch it runs from the session start (0).
    """
    if not ep.has_timing:
        return []
    out = []
    prev_end, prev_tool = 0.0, START
    for lo, hi in ep.batches:
        evs = ep.events[lo:hi]
        t = evs[0].t_offset_ms
        if t is None:
            return out
        for tool in {e.tool for e in evs}:
            out.append((prev_tool, tool, max(0.0, t - prev_end)))
        svc = [e.service_ms for e in evs if e.service_ms is not None]
        prev_end = t + (max(svc) if svc else 0.0)
        prev_tool = sorted(e.tool for e in evs)[-1]
    return out


class _CappedCounter:
    """Counts (table, key, target) with a per-session cap on each triple; each session's
    counts are multiplied by its weight (1 for train, <= 1 for logged traces)."""

    def __init__(self, cap: int | None) -> None:
        self.cap = math.inf if cap is None else cap
        self.counts: dict[str, dict[str, dict[str, float]]] = defaultdict(
            lambda: defaultdict(lambda: defaultdict(float)))
        self._session: Counter[tuple[str, str, str]] = Counter()
        self.weight = 1.0

    def new_session(self, weight: float = 1.0) -> None:
        self._session.clear()
        self.weight = weight

    def add(self, table: str, key: str, target: str, n: int = 1) -> None:
        trip = (table, key, target)
        room = self.cap - self._session[trip]
        if room <= 0:
            return
        n = int(min(n, room))
        self._session[trip] += n
        self.counts[table][key][target] += n * self.weight


def _wquantile(x: np.ndarray, w: np.ndarray, qs: Sequence[float]) -> list[float]:
    """Weighted quantiles (inverted CDF); equals np.percentile's 'inverted_cdf' for w = 1."""
    order = np.argsort(x, kind="stable")
    xs, cw = x[order], np.cumsum(w[order])
    total = cw[-1]
    return [float(xs[min(np.searchsorted(cw, q * total, side="left"), len(xs) - 1)])
            for q in qs]


def _normalise(c: dict[str, float]) -> dict[str, float]:
    total = sum(c.values())
    return {t: v / total for t, v in c.items()} if total > 0 else {}


class TransitionGraph(NextToolModel):
    """Count tables over train decision points; see the module docstring."""

    name = "transition_graph"

    def __init__(self, min_support: int = 3, per_session_cap: int | None = 3,
                 max_events: int = 2000, max_order: int = MAX_ORDER,
                 max_gap_ms: float = 3_600_000.0) -> None:
        self.min_support = min_support
        self.max_gap_ms = max_gap_ms
        self.per_session_cap = per_session_cap
        self.max_events = max_events
        self.max_order = max_order
        # table name -> context key -> {target: count}; table names:
        #   h<k>      tool of the k-th next batch (k = 1..5), END at the end point
        #   w<k>      tool appears within the next k batches (k in WITHIN)
        #   set       tool set of the next batch
        #   calls     calls per tool in the next batch (with multiplicity); "#" = contexts
        #   copies:<tool>  number of calls of <tool> in the next batch, when present
        #   node:h1   tool of the next batch given the workflow node
        self.tables: dict[str, dict[str, dict[str, float]]] = {}
        self.totals: dict[str, dict[str, float]] = {}
        self.timings: dict[str, list[float]] = {}  # "prev|next", "|next", "" -> [p10,p50,p90,n]
        self.winsor: tuple[float, float] = (0.0, math.inf)
        self.vocab: tuple[str, ...] = ()
        self.batch_stats: dict[str, Any] = {}
        self.train_hash = ""
        self.sanitize_stats: dict[str, int] = {}

    @classmethod
    def from_config(cls, path: Path = CONFIG) -> TransitionGraph:
        """Unfitted graph with the parameters in configs/model/graph.yaml."""
        cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
        return cls(**{k: cfg[k] for k in ("min_support", "per_session_cap", "max_events",
                                          "max_order", "max_gap_ms") if k in cfg})

    # ------------------------------------------------------------------ fitting
    def fit(self, train: Sequence[Episode], val: Sequence[Episode] = ()) -> TransitionGraph:
        eps, stats = sanitize(train, self.max_events)
        return self._fit(eps, [1.0] * len(eps), stats)

    def _fit(self, eps: Sequence[Episode], weights: Sequence[float],
             stats: Counter[str]) -> TransitionGraph:
        self.sanitize_stats = dict(stats)
        self.train_hash = data_hash(eps)
        cc = _CappedCounter(self.per_session_cap)
        vocab: set[str] = {END}
        sizes: Counter[int] = Counter()
        gaps: list[tuple[str, str, float, float]] = []
        for ep, w in zip(eps, weights, strict=True):
            cc.new_session(w)
            vocab.update(e.tool for e in ep.events)
            for lo, hi in ep.batches:
                sizes[hi - lo] += 1
            gaps.extend((a, b, g, w) for a, b, g in _think_gaps(ep))
            for i in range(ep.n_batches + 1):
                ctx = ep.context(i)
                keys = context_keys(ctx, self.max_order)
                nxt = ep.batch_tools(i) if i < ep.n_batches else [END]
                for k in HORIZONS:
                    j = i + k - 1
                    tg = (ep.batch_tools(j) if j < ep.n_batches
                          else [END] if j == ep.n_batches else [])
                    for t in set(tg):
                        for key in keys:
                            cc.add(f"h{k}", key, t)
                for k in WITHIN:
                    within = {t for j in range(i, min(i + k, ep.n_batches))
                              for t in ep.batch_tools(j)}
                    if i + k > ep.n_batches:
                        within.add(END)
                    for t in within:
                        for key in keys:
                            cc.add(f"w{k}", key, t)
                    for key in keys:
                        cc.add(f"w{k}", key, "#")
                sk = _set_key(nxt)
                per_tool = Counter(nxt)
                for key in keys:
                    cc.add("set", key, sk)
                    cc.add("calls", key, "#")
                    for t, n in per_tool.items():
                        cc.add("calls", key, t, n)
                if i < ep.n_batches:
                    for t, n in per_tool.items():
                        for key in keys[:2]:
                            cc.add(f"copies:{t}", key, str(n))
                if ctx.node is not None:
                    for t in set(nxt):
                        cc.add("node:h1", ctx.node, t)
        self._finish(cc.counts)
        self.vocab = tuple(sorted(vocab))
        n_b = sum(sizes.values())
        self.batch_stats = {
            "mean_batch_size": sum(s * n for s, n in sizes.items()) / max(n_b, 1),
            "batch_size_hist": {str(s): n for s, n in sorted(sizes.items())},
        }
        self.mean_batch_size = self.batch_stats["mean_batch_size"]
        self._fit_timing(gaps)
        return self

    def _finish(self, raw: dict[str, dict[str, dict[str, float]]]) -> None:
        """Drop pairs below min_support; keep the counts (JSON-ready, sorted)."""
        self.tables, self.totals = {}, {}
        for name in sorted(raw):
            table: dict[str, dict[str, float]] = {}
            totals: dict[str, float] = {}
            for key in sorted(raw[name]):
                c = raw[name][key]
                denom = c.get("#")
                kept = {t: float(v) for t, v in sorted(c.items())
                        if t != "#" and v >= self.min_support}
                if not kept:
                    continue
                table[key] = kept
                totals[key] = float(denom) if denom is not None else float(sum(kept.values()))
            if table:
                self.tables[name] = table
                self.totals[name] = totals

    def _fit_timing(self, gaps: list[tuple[str, str, float, float]]) -> None:
        """Weighted p10/p50/p90 per (prev, next), per next and global, after a hard clip to
        [0, max_gap_ms] and winsorising at the (weighted) p1/p99."""
        self.timings = {}
        gaps = [(a, b, g, w) for a, b, g, w in gaps if math.isfinite(g) and w > 0]
        if not gaps:
            self.winsor = (0.0, math.inf)
            return
        allg = np.clip(np.asarray([g for _, _, g, _ in gaps]), 0.0, self.max_gap_ms)
        allw = np.asarray([w for _, _, _, w in gaps])
        lo, hi = _wquantile(allg, allw, [0.01, 0.99])
        self.winsor = (lo, hi)
        groups: dict[str, tuple[list[float], list[float]]] = defaultdict(lambda: ([], []))
        for (a, b, _, w), g in zip(gaps, allg, strict=True):
            g = min(max(float(g), lo), hi)
            for key in (f"{a}{SEP}{b}", f"{SEP}{b}", ""):
                groups[key][0].append(g)
                groups[key][1].append(w)
        for key in sorted(groups):
            xs, ws = groups[key]
            support = sum(ws)
            if support < self.min_support and key != "":
                continue
            q = _wquantile(np.asarray(xs), np.asarray(ws), [0.1, 0.5, 0.9])
            self.timings[key] = [q[0], q[1], q[2], float(support)]

    # ------------------------------------------------------------------ lookups
    def _lookup(self, table: str, ctx: Context) -> tuple[dict[str, float], float] | None:
        t = self.tables.get(table)
        if not t:
            return None
        tot = self.totals[table]
        for key in reversed(context_keys(ctx, self.max_order)):
            if key in t and tot[key] >= self.min_support:
                return t[key], tot[key]
        return None

    def one_step(self, ctx: Context, node: str | None = None) -> dict[str, float]:
        """P(tool of the next batch); END included. Node-conditioned when the node is seen."""
        node = node if node is not None else ctx.node
        if node is not None:
            hit = self.tables.get("node:h1", {}).get(node)
            if hit and sum(hit.values()) >= self.min_support:
                return _normalise(hit)
        return self.predict(ctx, 1)

    def predict(self, ctx: Context, k: int = 1) -> dict[str, float]:
        """Distribution over the tools of the k-th next batch (sums to 1 when known)."""
        k = min(max(k, 1), max(HORIZONS))
        hit = self._lookup(f"h{k}", ctx)
        return _normalise(hit[0]) if hit else {END: 1.0}

    def k_step(self, ctx: Context, k: int) -> dict[str, float]:
        """P(tool appears at least once in the next k batches), k <= 5. Values are marginal
        probabilities and may sum above 1."""
        if k <= 1:
            return self.predict(ctx, 1)
        kk = min((w for w in WITHIN if w >= k), default=max(WITHIN))
        hit = self._lookup(f"w{kk}", ctx)
        if not hit:
            return {}
        counts, total = hit
        return {t: min(1.0, v / total) for t, v in counts.items()}

    def p_end(self, ctx: Context) -> float:
        return self.predict(ctx, 1).get(END, 0.0)

    def batch_next(self, ctx: Context) -> list[tuple[frozenset[str], float]]:
        """Distribution over the next batch's tool set, most likely first."""
        hit = self._lookup("set", ctx)
        if not hit:
            return [(frozenset({END}), 1.0)]
        probs = _normalise(hit[0])
        out = [(frozenset(k.split(SET_SEP)), p) for k, p in probs.items()]
        return sorted(out, key=lambda kv: (-kv[1], sorted(kv[0])))

    def copies(self, tool: str, ctx: Context | None = None) -> dict[int, float]:
        """P(the next batch has n calls of ``tool`` | the tool is in it), last-tool context
        when it has support, else the tool's global distribution; {1: 1.0} if unseen."""
        table = self.tables.get(f"copies:{tool}", {})
        keys = context_keys(ctx, 1) if ctx is not None else [""]
        for key in reversed(keys):
            c = table.get(key)
            if c and sum(c.values()) >= self.min_support:
                return {int(n): p for n, p in _normalise(c).items()}
        return {1: 1.0}

    def expected_calls(self, ctx: Context) -> dict[str, float]:
        """Expected number of calls per tool in the next batch for one session."""
        hit = self._lookup("calls", ctx)
        if not hit:
            return {}
        counts, total = hit
        return {t: v / total for t, v in counts.items()}

    def fleet_demand(self, ctxs: Iterable[Context]) -> dict[str, float]:
        """Expected calls per tool at the next step, summed over sessions."""
        out: dict[str, float] = defaultdict(float)
        for ctx in ctxs:
            for t, v in self.expected_calls(ctx).items():
                out[t] += v
        return dict(sorted(out.items()))

    def timing(self, prev: str, nxt: str) -> Timing:
        """Think-gap quantiles (ms) for prev -> next: the pair if it has support, else the
        next tool, else global; ``level`` says which was used."""
        for key, level in ((f"{prev}{SEP}{nxt}", "pair"), (f"{SEP}{nxt}", "next"),
                           ("", "global")):
            v = self.timings.get(key)
            if v is not None:
                return Timing(v[0], v[1], v[2], int(v[3]), level)
        return Timing(None, None, None, 0, "none")

    # ------------------------------------------------------------------ export
    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": "transition_graph",
            "params": {"min_support": self.min_support, "per_session_cap": self.per_session_cap,
                       "max_events": self.max_events, "max_order": self.max_order,
                       "max_gap_ms": self.max_gap_ms},
            "vocabulary": list(self.vocab),
            "train_hash": self.train_hash,
            "sanitize_stats": dict(sorted(self.sanitize_stats.items())),
            "counts": self.tables,
            "totals": self.totals,
            "timing": {"winsor_ms": list(self.winsor) if math.isfinite(self.winsor[1])
                       else [self.winsor[0], None],
                       "quantiles_p10_p50_p90_n": self.timings},
            "batch_stats": self.batch_stats,
        }

    def save(self, path: Path, version: str = "dev") -> None:
        d = self.to_dict()
        d["version"] = version
        path.write_text(json.dumps(d, sort_keys=True, separators=(",", ":")) + "\n",
                        encoding="utf-8")

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TransitionGraph:
        if d.get("schema_version") != SCHEMA_VERSION or d.get("kind") != "transition_graph":
            raise ValueError("unsupported counts schema")
        g = cls(**d["params"])
        g.vocab = tuple(d["vocabulary"])
        g.train_hash = d["train_hash"]
        g.sanitize_stats = dict(d.get("sanitize_stats", {}))
        g.tables = {n: {k: {t: float(v) for t, v in c.items()} for k, c in tab.items()}
                    for n, tab in d["counts"].items()}
        g.totals = {n: {k: float(v) for k, v in tab.items()} for n, tab in d["totals"].items()}
        w = d["timing"]["winsor_ms"]
        g.winsor = (float(w[0]), math.inf if w[1] is None else float(w[1]))
        g.timings = {k: [float(x) for x in v]
                     for k, v in d["timing"]["quantiles_p10_p50_p90_n"].items()}
        g.batch_stats = d["batch_stats"]
        g.mean_batch_size = float(g.batch_stats.get("mean_batch_size", 1.0))
        return g

    @classmethod
    def load(cls, path: Path) -> TransitionGraph:
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))


def graph_features(g: TransitionGraph, ctx: Context) -> dict[str, Any]:
    """Every graph feature at one decision point (used by the ranker and the leakage test)."""
    last = ctx.tools(1)
    prev = last[0] if last else START
    one = g.one_step(ctx)
    feats: dict[str, Any] = {"one_step": one, "p_end": g.p_end(ctx),
                             "batch_next": [(sorted(s), p) for s, p in g.batch_next(ctx)[:5]],
                             "expected_calls": g.expected_calls(ctx)}
    for k in WITHIN:
        feats[f"k_step_{k}"] = g.k_step(ctx, k)
    feats["timing"] = {t: g.timing(prev, t) for t in sorted(one) if t != END}
    feats["copies"] = {t: g.copies(t, ctx) for t in sorted(one) if t != END}
    return feats


def top1_accuracy(g: NextToolModel, eps: Sequence[Episode]) -> float:
    """Call-weighted next-call top-1 accuracy (END excluded from the ranking)."""
    hits = n = 0
    for ep in eps:
        for i in range(ep.n_batches):
            p = {t: v for t, v in g.predict(ep.context(i), 1).items() if t != END}
            best = min(p, key=lambda t: (-p[t], t)) if p else None
            for t in ep.batch_tools(i):
                n += 1
                hits += best == t
    return hits / n if n else math.nan


@dataclass(frozen=True)
class RebuildReport:
    accepted: bool
    reason: str
    base_val_top1: float
    candidate_val_top1: float
    logged_in: int
    logged_kept: int
    logged_weight: float


def rebuild(base_train: Sequence[Episode], logged: Sequence[Episode],
            val: Sequence[Episode], *, max_new_share: float = 0.5,
            max_val_drop: float = 0.01, **params: Any) -> tuple[TransitionGraph, RebuildReport]:
    """Re-train with newly logged traces, guarded against poisoning (module docstring).

    Logged sessions are sanitised, then weighted so that together they count as at most
    ``max_new_share`` x len(base_train) sessions. The candidate is accepted only if its
    top-1 on the clean ``val`` set is at most ``max_val_drop`` below the base graph's.
    """
    base_eps, base_stats = sanitize(base_train, params.get("max_events", 2000))
    base = TransitionGraph(**params)._fit(base_eps, [1.0] * len(base_eps), base_stats)
    new_eps, new_stats = sanitize(logged, params.get("max_events", 2000))
    w = min(1.0, max_new_share * len(base_eps) / len(new_eps)) if new_eps else 0.0
    stats = base_stats + Counter({f"logged_{k}": v for k, v in new_stats.items()})
    cand = TransitionGraph(**params)._fit(list(base_eps) + list(new_eps),
                                          [1.0] * len(base_eps) + [w] * len(new_eps), stats)
    b, c = top1_accuracy(base, val), top1_accuracy(cand, val)
    ok = not (c < b - max_val_drop)
    reason = ("accepted" if ok else
              f"rejected: val top-1 {c:.4f} is more than {max_val_drop:.3f} below {b:.4f}")
    report = RebuildReport(ok, reason, b, c, len(logged), len(new_eps), w * len(new_eps))
    return (cand if ok else base), report
