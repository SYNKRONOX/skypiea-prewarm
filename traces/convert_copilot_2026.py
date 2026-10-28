"""GitHub Copilot coding agent traces 2026 (Azure Public Dataset) converter.

Reads the per-day ``date.<day>.tar.gz`` archives in data/raw/copilot_2026/ and streams the
``shard-NNNN.jsonl.gz`` members one at a time. Each line is one session.

- batch: one entry of ``turns[].tool_batches`` (empty batches are skipped and counted).
- t_offset_ms: batch ``timestamp`` (taken as the batch start) relative to the session's
  first batch. LLM-call timestamps do not line up with batch timestamps, so they are
  not used.
- service_ms: per-call ``duration_ms``.
- error: ``status == 2`` (failure or timeout). Status 0 is undocumented; it is treated as
  not an error and counted.
- group: ``session_id``. The release has no user, repository or organisation id.
- tool names: built-in tools are plain; custom tools are anonymised as ``tool_<hex>``.

Option ``shards=N`` stops after N shard files.
"""

from __future__ import annotations

import gzip
import json
import tarfile
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from traces.schema import Batch, Call, Session
from traces.timeutil import ms_between, parse_ts


def _session(d: dict[str, Any], stats: Counter[str]) -> Session:
    raw_batches = []
    for turn in d["turns"]:
        for b in turn["tool_batches"]:
            if not b["function_calls"]:
                stats["empty_batches_skipped"] += 1
                continue
            raw_batches.append(b)
    times = [parse_ts(b["timestamp"]) for b in raw_batches]
    t0 = min(times) if times else None
    batches = []
    for b, t in zip(raw_batches, times, strict=True):
        calls = []
        for c in b["function_calls"]:
            if c["status"] == 0:
                stats["calls_status_0"] += 1
            if c["name"].startswith("tool_"):
                stats["calls_anonymised_name"] += 1
            dur = c.get("duration_ms")
            calls.append(Call(c["name"], None if dur is None else float(dur),
                              c["status"] == 2, c["name"]))
        batches.append(Batch(len(batches), ms_between(t0, t), None, calls))  # type: ignore[arg-type]
    return Session(d["session_id"], "copilot_2026", d["session_id"], True, batches)


def convert(raw: Path, stats: Counter[str], **opts: Any) -> Iterator[Session]:
    max_shards = opts.get("shards")
    archives = sorted((raw / "copilot_2026").glob("*.tar.gz"))
    if not archives:
        raise FileNotFoundError("no copilot_2026 archives; run data/download.py first")
    shards = 0
    for archive in archives:
        with tarfile.open(archive, "r|gz") as tf:
            for m in tf:
                if not (m.isfile() and m.name.endswith(".jsonl.gz")):
                    continue
                if max_shards is not None and shards >= max_shards:
                    return
                shards += 1
                stats["shards_read"] += 1
                member = tf.extractfile(m)
                assert member is not None
                with gzip.open(member, "rt", encoding="utf-8") as f:
                    for line in f:
                        yield _session(json.loads(line), stats)
