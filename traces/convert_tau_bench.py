"""tau-bench (v1) historical trajectories converter.

Each trajectory in ``historical_trajectories/*.json`` is one session.

- batch: one assistant message with ``tool_calls`` (in practice always one call).
- timing: none in the source, so ``has_timing=False`` and all times are ``None``.
- error: the tool reply starts with ``Error:``, the convention tau-bench's ``env.step``
  uses when a tool raises. Only that prefix is read; reply text is not kept.
  Replies are paired with calls by position (the tool messages right after the assistant
  message), not by id: the Sonnet trajectories reuse tool-call ids within a trajectory.
  A reply whose ``name`` differs from its call is counted as ``reply_name_mismatch``.
- group: ``<domain>:<task_id>``, so every model and trial of a task lands in one split.

Each file is a single JSON array, so it is loaded one file at a time (largest is 26 MB).
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from traces.schema import Batch, Call, Session


def convert(raw: Path, stats: Counter[str], **opts: Any) -> Iterator[Session]:
    files = sorted((raw / "tau_bench" / "historical_trajectories").glob("*.json"))
    if not files:
        raise FileNotFoundError("no tau_bench trajectories; run data/download.py first")
    for path in files:
        domain = "airline" if "airline" in path.stem else "retail"
        for t in json.loads(path.read_text(encoding="utf-8")):
            traj = t["traj"]
            batches = []
            for i, m in enumerate(traj):
                tcs = m.get("tool_calls") or []
                if m["role"] != "assistant" or not tcs:
                    continue
                replies = []
                for r in traj[i + 1:]:
                    if r["role"] != "tool":
                        break
                    replies.append(r)
                calls = []
                for k, c in enumerate(tcs):
                    name = c["function"]["name"]
                    reply = replies[k] if k < len(replies) else None
                    if reply is None:
                        stats["calls_without_reply"] += 1
                    elif reply.get("name") != name:
                        stats["reply_name_mismatch"] += 1
                    failed = reply is not None and str(reply.get("content") or "").startswith(
                        "Error:"
                    )
                    calls.append(Call(name, None, failed, name))
                batches.append(Batch(len(batches), None, None, calls))
            sid = f"{path.stem}:{t['task_id']}:{t['trial']}"
            yield Session(sid, "tau_bench", f"{domain}:{t['task_id']}", False, batches)
