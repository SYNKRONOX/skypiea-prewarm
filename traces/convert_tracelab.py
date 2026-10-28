"""TraceLab converter (uw-syfi/TraceLab v0.0.2, syfi_coding_trace.jsonl.gz).

Each row is one LLM round. All tools emitted in a round form one batch.

- t_offset_ms: earliest ``emitted_at`` in the round, relative to the session's earliest
  timing event (usually the user message).
- service_ms: ``tool_wall_latency_ms`` (equal to ``result_at - emitted_at``).
- error: ``is_error``; null is stored as False and counted.
- group: ``user``.

Resumed Claude sessions repeat calls from an earlier session of the same user (same call
id, tool name and emit time). Those copies are dropped and counted. Rows are streamed
and grouped by consecutive ``session_id``; a session whose rows are not contiguous yields
a second fragment with the same id, which the pipeline drops as a duplicate.
"""

from __future__ import annotations

import gzip
import json
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from traces.schema import Batch, Call, Session
from traces.timeutil import ms_between, parse_ts

FILE = "tracelab/syfi_coding_trace.jsonl.gz"


def _session(sid: str, rows: list[dict[str, Any]], seen: set[tuple[str, str, str]],
             stats: Counter[str]) -> Session:
    rows.sort(key=lambda r: r["round_index"])
    starts = [e["timestamp"] for r in rows for e in r["timing_events"] if e.get("timestamp")]
    t0 = parse_ts(min(starts)) if starts else None
    batches = []
    for r in rows:
        calls, emits = [], []
        for t in r["tools"]:
            key = (t["tool_call_id"], t["tool_name"], t.get("emitted_at") or "")
            if key in seen:
                stats["copied_calls_dropped"] += 1
                continue
            seen.add(key)
            if t.get("is_error") is None:
                stats["calls_error_null"] += 1
            lat = t.get("tool_wall_latency_ms")
            calls.append(Call(t["tool_name"], None if lat is None else float(lat),
                              bool(t.get("is_error")), t["tool_name"]))
            if t.get("emitted_at"):
                emits.append(parse_ts(t["emitted_at"]))
        if not calls:
            continue
        if t0 is None or not emits:
            stats["batches_without_time_dropped"] += 1
            continue
        batches.append(Batch(len(batches), ms_between(t0, min(emits)), None, calls))
    return Session(sid, "tracelab", rows[0]["user"], True, batches)


def convert(raw: Path, stats: Counter[str], **opts: Any) -> Iterator[Session]:
    seen: set[tuple[str, str, str]] = set()
    current: str | None = None
    rows: list[dict[str, Any]] = []
    with gzip.open(raw / FILE, "rt", encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            if d["session_id"] != current:
                if rows:
                    yield _session(current, rows, seen, stats)  # type: ignore[arg-type]
                current, rows = d["session_id"], []
            # Keep only the metadata we need; text-derived fields are not read.
            rows.append({
                "round_index": d["round_index"],
                "user": d["user"],
                "timing_events": [{"timestamp": e.get("timestamp")} for e in d["timing_events"]],
                "tools": d["tools"],
            })
    if rows:
        yield _session(current, rows, seen, stats)  # type: ignore[arg-type]
