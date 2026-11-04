"""Request parsing and hardening for ``POST /decide`` (contract v1 + decisions P2-P8, P-new).

Pure functions, no I/O. ``parse_request`` never raises on a JSON object: anything wrong
becomes a repaired value plus a flag (the caller counts flags by name; values never reach
logs or errors), and a request that cannot be used at all becomes ``Parsed.bad`` (the service
then answers 200 with no decisions, the cold path). Only body size and history length beyond
``MAX_ITEMS`` are refused outright (413).

Rules:
- ``session_id``: ``^[A-Za-z0-9_.-]{1,64}$`` (P2; the gateway sends a hash). Otherwise bad.
  The service never stores or logs it and never uses it as a metric label.
- ``now_ms``: finite, in [0, 1e9]; otherwise clipped, flag ``now_ms`` (P5: validated, unused).
- ``history``: a list; more than ``MAX_ITEMS`` items -> 413; the last ``MAX_HISTORY`` are used
  and ``truncated`` is set (P3: the gateway sends at most 200 plus ``history_total``).
- ``tool_class``: lowercased; must match ``^[a-z0-9_]{1,48}$``, else ``<unk>`` (flag).
- ``t_start_ms`` / ``t_end_ms``: finite, in [0, 1e9], else clipped (NaN/None -> 0), flag;
  service time = max(0, end - start).
- ``batch_id``: renumbered in arrival order: consecutive items with the same raw id form one
  batch, any change starts the next one (so duplicates and decreasing ids are repaired
  deterministically); flag if the raw ids were not non-decreasing.
- ``error``: must be a bool, else False (flag).
- ``history_total``: optional int >= len(history); otherwise ignored (flag).
- ``oracle`` and unknown fields: ignored.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from predictors.data import MAX_HISTORY, Event

SESSION_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
TOOL_RE = re.compile(r"^[a-z0-9_]{1,48}$")
UNK = "<unk>"
MAX_BODY_BYTES = 256 * 1024
MAX_ITEMS = 1000
T_MAX = 1e9


class TooLarge(ValueError):
    """The request is over a hard limit (HTTP 413)."""


@dataclass
class Parsed:
    events: list[Event] = field(default_factory=list)
    truncated: bool = False
    history_total: int | None = None
    flags: Counter[str] = field(default_factory=Counter)
    bad: str | None = None  # reason the request cannot be used (answer: no decisions)


def _time(v: Any, flags: Counter[str], name: str) -> float:
    if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v):
        flags[name] += 1
        return 0.0
    if not 0.0 <= v <= T_MAX:
        flags[name] += 1
        return float(min(max(v, 0.0), T_MAX))
    return float(v)


def tool_name(v: Any, flags: Counter[str]) -> str:
    if isinstance(v, str):
        t = v.lower()
        if TOOL_RE.match(t):
            return t
    flags["tool_class"] += 1
    return UNK


def parse_request(d: Any) -> Parsed:
    out = Parsed()
    f = out.flags
    if not isinstance(d, dict):
        out.bad = "not_object"
        return out
    sid = d.get("session_id")
    if not isinstance(sid, str) or not SESSION_RE.match(sid):
        out.bad = "session_id"
        return out
    if "now_ms" in d:
        _time(d.get("now_ms"), f, "now_ms")
    hist = d.get("history")
    if not isinstance(hist, list):
        out.bad = "history"
        return out
    if len(hist) > MAX_ITEMS:
        raise TooLarge("history")
    if len(hist) > MAX_HISTORY:
        hist = hist[-MAX_HISTORY:]
        out.truncated = True
    tot = d.get("history_total")
    if tot is not None:
        if isinstance(tot, int) and not isinstance(tot, bool) and tot >= len(d["history"]):
            out.history_total = tot
        else:
            f["history_total"] += 1
    raw_ids: list[Any] = []
    rows: list[tuple[str, float, float, bool]] = []
    for h in hist:
        if not isinstance(h, dict):
            f["item"] += 1
            h = {}
        tool = tool_name(h.get("tool_class"), f)
        t0 = _time(h.get("t_start_ms"), f, "t_start_ms")
        t1 = _time(h.get("t_end_ms"), f, "t_end_ms")
        err = h.get("error", False)
        if not isinstance(err, bool):
            f["error"] += 1
            err = False
        b = h.get("batch_id")
        if isinstance(b, bool) or not isinstance(b, int):
            f["batch_id"] += 1
            b = None
        raw_ids.append(b)
        rows.append((tool, t0, t1, err))
    ok = [b for b in raw_ids if b is not None]
    if any(y < x for x, y in zip(ok, ok[1:], strict=False)):
        f["batch_id"] += 1
    bid, prev = -1, object()
    for (tool, t0, t1, err), b in zip(rows, raw_ids, strict=True):
        if b is None or b != prev:
            bid += 1
        prev = b
        out.events.append(Event(tool, t0, bid, max(0.0, t1 - t0), err))
    return out
