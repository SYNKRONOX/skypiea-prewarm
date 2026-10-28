"""Timestamp helpers shared by the converters."""

from __future__ import annotations

from datetime import datetime


def parse_ts(s: str) -> datetime:
    """Parse an ISO-8601 timestamp, including a trailing ``Z`` and nanosecond digits."""
    s = s.replace("Z", "+00:00")
    if "." in s:
        head, frac = s.split(".", 1)
        digits = "".join(ch for ch in frac if ch.isdigit())
        s = f"{head}.{digits[:6]}{frac[len(digits):]}"
    return datetime.fromisoformat(s)


def ms_between(a: datetime, b: datetime) -> float:
    """Milliseconds from ``a`` to ``b`` (negative if ``b`` is earlier)."""
    return round((b - a).total_seconds() * 1000, 3)
