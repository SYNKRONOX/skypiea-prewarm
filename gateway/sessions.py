"""Per-session state. Sessions are fully isolated; ended or idle sessions are purged (NFR-6)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from gateway.contract import HistoryItem, OracleHint


@dataclass
class Session:
    session_id: str
    created: float
    last_seen: float
    history: list[HistoryItem] = field(default_factory=list)
    oracle: list[OracleHint] = field(default_factory=list)
    inflight: int = 0
    batch_id: int = -1
    ended_at: float | None = None

    def start_call(self) -> int:
        """Calls that overlap an in-flight call of the same session share its batch."""
        if self.inflight == 0:
            self.batch_id += 1
        self.inflight += 1
        return self.batch_id


class SessionStore:
    def __init__(self, idle_s: float = 600.0, clock=time.monotonic):
        self.idle_s, self.clock = idle_s, clock
        self._s: dict[str, Session] = {}

    def get(self, sid: str) -> Session:
        now = self.clock()
        s = self._s.get(sid)
        if s is None or s.ended_at is not None:  # an ended id starts a fresh session
            s = Session(sid, now, now)
            self._s[sid] = s
        s.last_seen = now
        return s

    def end(self, sid: str) -> bool:
        s = self._s.get(sid)
        if s is None:
            return False
        s.ended_at = self.clock()
        return True

    def purge(self) -> int:
        """Drop ended sessions (no in-flight calls) and sessions idle for idle_s."""
        now = self.clock()
        dead = [k for k, s in self._s.items() if s.inflight == 0 and (
            s.ended_at is not None or now - s.last_seen > self.idle_s)]
        for k in dead:
            del self._s[k]
        return len(dead)

    def __len__(self) -> int:
        return len(self._s)

    def __contains__(self, sid: str) -> bool:
        return sid in self._s
