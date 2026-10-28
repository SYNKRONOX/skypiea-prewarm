"""tau2-bench published results converter.

Each simulation in ``data/tau2/results/**/*.json`` is one session.

- batch: one assistant message with ``tool_calls``.
- t_offset_ms: assistant message ``timestamp`` relative to the simulation ``start_time``.
- service_ms: matching tool reply ``timestamp`` minus the assistant message timestamp.
  These come from a simulated run with in-process tools, so they are a few ms and do not
  represent real MCP tool run time.
- error: the tool reply's ``error`` flag.
- group: ``<domain>:<task_id>``.

Each results file is one JSON object, so it is loaded one file at a time.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from traces.schema import Batch, Call, Session
from traces.timeutil import ms_between, parse_ts

# Longest first, so "telecom-workflow" wins over "telecom".
DOMAINS = ["banking_knowledge", "telecom-workflow", "telecom", "airline", "retail", "mock"]


def convert(raw: Path, stats: Counter[str], **opts: Any) -> Iterator[Session]:
    files = sorted((raw / "tau2_bench" / "data" / "tau2" / "results").rglob("*.json"))
    if not files:
        raise FileNotFoundError("no tau2_bench results; run data/download.py first")
    for path in files:
        domain = next((d for d in DOMAINS if f"_{d}_" in path.name), "unknown")
        for s in json.loads(path.read_text(encoding="utf-8"))["simulations"]:
            t0 = parse_ts(s["start_time"])
            replies = {
                m["id"]: (parse_ts(m["timestamp"]), bool(m.get("error")))
                for m in s["messages"]
                if m["role"] == "tool"
            }
            batches = []
            for m in s["messages"]:
                tcs = m.get("tool_calls") or []
                if m["role"] != "assistant" or not tcs:
                    continue
                emit = parse_ts(m["timestamp"])
                calls = []
                for c in tcs:
                    if c["id"] in replies:
                        done, err = replies[c["id"]]
                        service: float | None = ms_between(emit, done)
                    else:
                        stats["calls_without_reply"] += 1
                        service, err = None, False
                    calls.append(Call(c["name"], service, err, c["name"]))
                batches.append(Batch(len(batches), ms_between(t0, emit), None, calls))
            yield Session(s["id"], "tau2_bench", f"{domain}:{s['task_id']}", True, batches)
