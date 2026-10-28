"""Loader API over the pipeline outputs in data/processed/<source>/.

    from traces.load import load_sequences
    train = load_sequences("tau_bench", "train")
    test = load_sequences("tau_bench", "test")

``split`` is "train", "val", "test", a list of those, or None for everything. ``level``
is "class" (mapped tool classes, the default) or "raw" (the source's own tool names).
Pass ``root=SAMPLES`` to read the small committed samples instead (used by tests and CI).
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any, Literal

import pandas as pd

from traces.schema import Session

ROOT = Path(__file__).resolve().parent.parent
PROCESSED = ROOT / "data" / "processed"
SAMPLES = ROOT / "data" / "samples"

SplitArg = str | Iterable[str] | None
Level = Literal["class", "raw"]


def _splits(split: SplitArg) -> set[str] | None:
    if split is None:
        return None
    return {split} if isinstance(split, str) else set(split)


def sessions_path(source: str, root: Path = PROCESSED) -> Path:
    for p in (root / source / "sessions.jsonl.gz", root / f"{source}_sample.jsonl.gz"):
        if p.exists():
            return p
    raise FileNotFoundError(
        f"no processed data for {source!r} under {root}; "
        f"run: python -m traces.pipeline --source {source}"
    )


def iter_session_dicts(source: str, split: SplitArg = None,
                       root: Path = PROCESSED) -> Iterator[dict[str, Any]]:
    """Stream session dicts (with their ``split`` field) without building objects."""
    wanted = _splits(split)
    with gzip.open(sessions_path(source, root), "rt", encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            if wanted is None or d["split"] in wanted:
                yield d


def load_sessions(source: str, split: SplitArg = "train",
                  root: Path = PROCESSED) -> list[Session]:
    return [Session.from_dict(d) for d in iter_session_dicts(source, split, root)]


def _name(call: Any, level: Level) -> str:
    return call.tool if level == "class" else (call.raw_tool or call.tool)


def load_sequences(source: str, split: SplitArg = "train", root: Path = PROCESSED,
                   level: Level = "class") -> list[list[str]]:
    """Flat tool sequences (calls in a batch sorted by name), for Markov and n-gram models."""
    out = []
    for s in load_sessions(source, split, root):
        out.append([_name(c, level) for b in s.batches
                    for c in sorted(b.calls, key=lambda c: _name(c, level))])
    return out


def load_batches(source: str, split: SplitArg = "train", root: Path = PROCESSED,
                 level: Level = "class") -> list[list[frozenset[str]]]:
    """Per session, the set of tools in each non-empty batch (for parallel prediction)."""
    return [
        [frozenset(_name(c, level) for c in b.calls) for b in s.batches if b.calls]
        for s in load_sessions(source, split, root)
    ]


def load_calls_df(source: str, split: SplitArg = "train",
                  root: Path = PROCESSED) -> pd.DataFrame:
    """One row per call, from calls.parquet (or built from the sessions for samples)."""
    wanted = _splits(split)
    parquet = root / source / "calls.parquet"
    if parquet.exists():
        filters = [("split", "in", sorted(wanted))] if wanted else None
        return pd.read_parquet(parquet, filters=filters)
    rows = []
    for d in iter_session_dicts(source, split, root):
        for b in d["batches"]:
            for pos, c in enumerate(b["calls"]):
                rows.append({
                    "session_id": d["session_id"], "source": d["source"], "group": d["group"],
                    "split": d["split"], "has_timing": d["has_timing"],
                    "batch_id": b["batch_id"], "position_in_batch": pos,
                    "batch_size": len(b["calls"]), "t_offset_ms": b["t_offset_ms"],
                    "service_ms": c["service_ms"], "tool": c["tool"],
                    "raw_tool": c["raw_tool"], "error": c["error"],
                })
    return pd.DataFrame(rows)


def load_all(sources: Iterable[str], split: SplitArg = "train",
             root: Path = PROCESSED) -> list[Session]:
    """Sessions from several sources combined (each keeps its ``source`` field)."""
    return [s for src in sources for s in load_sessions(src, split, root)]
