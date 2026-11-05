"""Warm-pool logic of the Skypiea controller (pure: no Kubernetes, no I/O, injectable clock).

Rules (docs/gateway_controller.md):
  * warm(server, copies, ttl): keep >= copies replicas until now + ttl; a later warm extends.
    Missing ttl -> default_ttl_s (30 s). A pod is removed at TTL + purge_s (5 s) (FR-3).
  * never scale DOWN a server while the gateway reports calls in flight on it, and keep at
    least 1 replica while it has any (pods serve many calls; no scale-up per call).
  * min_dwell_s between two replica changes of a server before a scale-DOWN (no flapping);
    scale-ups are never delayed.
  * hard caps: max_per_tool replicas per server and max_total across the cluster; anything
    above is refused and counted, never applied.
  * max_scaleups_per_node (default 2): at most that many pods per schedulable node may be
    starting (replicas > ready) at once; E0 on the 2-vCPU node measured 3 parallel cold
    starts at 5.5 s median even with the image cached vs 2.7 s alone (app_init 3.7 s vs
    1.2 s). Excess scale-ups wait in a queue ordered by when they are due (request time +
    start_in_ms; calls already waiting first on ties) and get the next free slot. Nothing
    is dropped silently: a queued request that expires before it got a slot is counted.
  * idempotent: plan() only emits changes where desired != live; re-applying is harmless.
  * restart-safe: rebuild() restores warm state from the live replica count and the
    skypiea/last-used and skypiea/ttl-s annotations that every scale patch writes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

ANN_LAST_USED = "skypiea/last-used"  # unix seconds of the last warm/demand for this tool
ANN_TTL = "skypiea/ttl-s"            # TTL that applied to that last use


@dataclass
class PoolConfig:
    default_ttl_s: float = 30.0
    purge_s: float = 5.0
    min_dwell_s: float = 5.0
    max_per_tool: int = 3
    max_total: int = 20
    inflight_stale_s: float = 30.0
    pending_warn_s: float = 120.0
    always_warm: bool = False   # baseline: every server keeps >= 1 replica
    max_scaleups_per_node: int = 2   # pods starting at once per node; 0 = no limit
    nodes: int = 1                   # schedulable nodes (set by the chart; no node RBAC)


@dataclass
class ServerState:
    live: int = 0                 # spec.replicas as last seen/applied
    ready: int = 0                # status.readyReplicas
    copies: int = 0               # requested warm copies
    warm_until: float = 0.0       # absolute time the warm request expires (before purge)
    last_used: float | None = None  # monotonic time of the last warm/demand/in-flight use
    ttl_s: float = 0.0
    last_change: float = -1e18    # time of the last replica change we applied/observed
    pending_since: float | None = None
    inflight: dict[str, tuple[int, float]] = field(default_factory=dict)  # gateway -> (n, at)
    due_at: float | None = None   # earliest time a pending scale-up is wanted (queue order)
    queued_since: float | None = None  # set while a scale-up waits for a free slot


@dataclass(frozen=True)
class Action:
    server: str
    from_replicas: int
    to_replicas: int
    reason: str
    annotations: dict[str, str]

    @property
    def direction(self) -> str:
        return "up" if self.to_replicas > self.from_replicas else "down"


class WarmPool:
    def __init__(self, servers: list[str], cfg: PoolConfig | None = None):
        self.cfg = cfg or PoolConfig()
        self.s: dict[str, ServerState] = {name: ServerState() for name in servers}
        self.refused: list[tuple[str, int, str]] = []  # (server, copies refused, reason)
        # scale-up queue bookkeeping, drained by the service into metrics
        self.queued: list[str] = []                  # server entered the queue
        self.dequeued: list[tuple[str, float]] = []  # (server, seconds waited) got its slots
        self.expired: list[tuple[str, float]] = []   # (server, seconds waited) wish ended first
        self.queue: list[tuple[str, int]] = []       # current queue after the last plan()

    # ---------------- inputs ----------------
    def warm(self, server: str, copies: int, ttl_s: float | None, now: float,
             start_in_ms: float = 0) -> int:
        """Register a warm request. Returns how many copies were accepted (<= requested)."""
        st = self.s[server]
        ttl = self.cfg.default_ttl_s if ttl_s is None else float(ttl_s)
        want = max(0, int(copies))
        accepted = min(want, self.cfg.max_per_tool)
        if want > accepted:
            self.refused.append((server, want - accepted, "per_tool_cap"))
        others = sum(self._desired(n, x, now) for n, x in self.s.items() if n != server)
        room = max(0, self.cfg.max_total - others)
        if accepted > room:
            self.refused.append((server, accepted - room, "cluster_cap"))
            accepted = room
        active = now < st.warm_until + self.cfg.purge_s
        st.copies = max(st.copies, accepted) if active else accepted
        if accepted > 0 or ttl_s is not None:
            st.warm_until = max(st.warm_until if active else 0.0, now + ttl)
            st.ttl_s = ttl
        st.last_used = now
        if accepted > 0:
            due = now + max(0.0, float(start_in_ms)) / 1000.0
            st.due_at = due if st.due_at is None else min(st.due_at, due)
        return accepted

    def report_inflight(self, gateway_id: str, counts: dict[str, int], now: float) -> None:
        """A server is "used" while calls are in flight on it; its TTL counts from the end of
        the last call (FR-3: removed TTL + purge_s after its last use)."""
        for name, st in self.s.items():
            n = int(counts.get(name, 0))
            was_busy = gateway_id in st.inflight
            if n > 0:
                st.inflight[gateway_id] = (n, now)
                st.due_at = now if st.due_at is None else min(st.due_at, now)
            else:
                st.inflight.pop(gateway_id, None)
            if n > 0 or was_busy:
                st.last_used = now
                if st.copies > 0:
                    st.warm_until = max(st.warm_until, now + st.ttl_s)

    def observe(self, server: str, live: int, ready: int, now: float) -> None:
        """Live state from the cluster (Deployment watch)."""
        st = self.s[server]
        if live != st.live:
            st.last_change = now
        st.live, st.ready = live, ready
        if live > 0 and ready == 0:
            st.pending_since = st.pending_since if st.pending_since is not None else now
        else:
            st.pending_since = None

    def rebuild(self, server: str, live: int, ready: int, annotations: dict[str, str],
                now_wall: float, now: float) -> None:
        """After a restart: restore warm state from the live Deployment."""
        st = self.s[server]
        st.live, st.ready, st.last_change = live, ready, now
        try:
            last = float(annotations.get(ANN_LAST_USED, ""))
            ttl = float(annotations.get(ANN_TTL, self.cfg.default_ttl_s))
        except ValueError:
            last, ttl = None, self.cfg.default_ttl_s
        if live > 0:
            # remaining warm time measured on the wall clock, mapped onto our monotonic clock;
            # without an annotation the pod gets one default TTL from now (never killed blind)
            remaining = (last + ttl - now_wall) if last is not None else ttl
            st.copies = min(live, self.cfg.max_per_tool)
            st.warm_until = now + max(0.0, remaining)
            st.ttl_s = ttl
            st.last_used = now - (now_wall - last) if last is not None else now

    # ---------------- decisions ----------------
    def inflight(self, server: str, now: float) -> int:
        st = self.s[server]
        return sum(n for n, at in st.inflight.values() if now - at <= self.cfg.inflight_stale_s)

    def _desired(self, name: str, st: ServerState, now: float) -> int:
        warm = st.copies if now < st.warm_until + self.cfg.purge_s else 0
        d = max(warm, 1 if self.cfg.always_warm else 0)
        busy = sum(n for n, at in st.inflight.values() if now - at <= self.cfg.inflight_stale_s)
        if busy > 0:
            d = max(d, 1, min(st.live, self.cfg.max_per_tool))  # never shrink under live calls
        return min(d, self.cfg.max_per_tool)

    def starting(self, now: float) -> int:
        """Pods requested but not Ready yet (stuck ones excluded: they would hold a slot
        forever; they are reported by stuck())."""
        return sum(max(0, st.live - st.ready) for st in self.s.values()
                   if not (st.pending_since is not None
                           and now - st.pending_since > self.cfg.pending_warn_s))

    def plan(self, now: float, now_wall: float) -> list[Action]:
        actions, ups = [], []
        total_live = sum(st.live for st in self.s.values())
        for name, st in self.s.items():
            d = self._desired(name, st, now)
            if d <= st.live and st.queued_since is not None:   # wish ended while queued
                self.expired.append((name, now - st.queued_since))
                st.queued_since = None
            if d <= st.live:
                st.due_at = None
            if d == st.live:
                continue
            if d < st.live:
                if now - st.last_change < self.cfg.min_dwell_s:
                    continue
                busy = self.inflight(name, now) > 0
                reason = "ttl_expired" if not busy else "inflight_floor"
                total_live += d - st.live
                actions.append(self._action(name, st, d, reason, now, now_wall))
            else:
                ups.append((name, d))
        per_node = self.cfg.max_scaleups_per_node
        slots = (per_node * max(1, self.cfg.nodes) - self.starting(now)) if per_node > 0 \
            else 1 << 30
        # earliest due first; on ties a server with calls already waiting first, then FIFO
        ups.sort(key=lambda x: (self.s[x[0]].due_at if self.s[x[0]].due_at is not None
                                else now, 0 if self.inflight(x[0], now) else 1,
                                self.s[x[0]].queued_since or now, x[0]))
        self.queue = []
        for name, d in ups:
            st = self.s[name]
            room = self.cfg.max_total - total_live
            if room <= 0:
                self.refused.append((name, d - st.live, "cluster_cap"))
                continue
            d = min(d, st.live + room)
            grant = min(d - st.live, max(0, slots))
            if grant < d - st.live:
                self.queue.append((name, d - st.live - grant))
                if st.queued_since is None:
                    st.queued_since = now
                    self.queued.append(name)
            elif st.queued_since is not None:
                self.dequeued.append((name, now - st.queued_since))
                st.queued_since = None
            if grant == 0:
                continue
            slots -= grant
            to = st.live + grant
            reason = "inflight" if self.inflight(name, now) and st.copies == 0 else "warm"
            if self.cfg.always_warm and st.copies == 0 and not self.inflight(name, now):
                reason = "always_warm"
            total_live += grant
            if to == d:
                st.due_at = None
            actions.append(self._action(name, st, to, reason, now, now_wall))
        return actions

    def _action(self, name: str, st: ServerState, to: int, reason: str, now: float,
                now_wall: float) -> Action:
        ann = {ANN_LAST_USED: f"{now_wall - (now - st.last_used):.3f}",
               ANN_TTL: f"{st.ttl_s:g}"} if st.last_used is not None else {}
        return Action(name, st.live, to, reason, ann)

    def applied(self, action: Action, now: float) -> None:
        st = self.s[action.server]
        st.live, st.last_change = action.to_replicas, now

    def idle_pods(self, server: str, now: float) -> int:
        """Ready pods with no call in flight (for the pod_seconds_idle metric)."""
        st = self.s[server]
        return max(0, st.ready - self.inflight(server, now))

    def stuck(self, now: float) -> list[str]:
        return [n for n, st in self.s.items() if st.pending_since is not None
                and now - st.pending_since > self.cfg.pending_warn_s]
