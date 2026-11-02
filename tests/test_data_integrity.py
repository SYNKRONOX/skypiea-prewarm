"""Leakage and integrity checks on the pipeline outputs and the committed samples.

For every source: no group in two splits, every tool in its vocabulary (and in the shared
vocabularies that cover the source), no string over 64 characters, and no field that could
carry prompt, message or argument text. Processed data is checked when present (it is not in
git); the committed samples are always checked.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import pytest

from traces.load import PROCESSED, SAMPLES, iter_session_dicts
from traces.tool_classes import load_mapper
from traces.vocab import VOCAB_DIR, load_vocab

SOURCES = ["tracelab", "copilot_2026", "tau_bench", "tau2_bench", "bfcl",
           "synthetic_deterministic", "synthetic_branching", "synthetic_random_walk"]
MAX_TEXT = 64
SESSION_KEYS = {"session_id", "source", "group", "has_timing", "trace_type", "batches", "split"}
BATCH_KEYS = {"batch_id", "t_offset_ms", "node", "calls"}
CALL_KEYS = {"tool", "service_ms", "error", "raw_tool"}
PARQUET_COLS = {"session_id", "source", "group", "split", "has_timing", "batch_id",
                "position_in_batch", "batch_size", "t_offset_ms", "service_ms", "tool",
                "raw_tool", "error"}
# Identifiers and tool names are single tokens. Prompt or message text has spaces, newlines
# or punctuation that this pattern rejects.
TOKEN = re.compile(r"^[A-Za-z0-9_.:/<>#@+\-]{1,64}$")
TEXTY_KEY = re.compile(r"prompt|message|content|text|argument|command|query|input|output|"
                       r"skeleton|executable", re.IGNORECASE)


def _cases() -> Iterator[Any]:
    for src in SOURCES:
        if (SAMPLES / f"{src}_sample.jsonl.gz").exists():
            yield pytest.param(SAMPLES, src, id=f"sample-{src}")
        if (PROCESSED / src / "sessions.jsonl.gz").exists():
            yield pytest.param(PROCESSED, src, id=f"processed-{src}")


CASES = list(_cases())


def _vocabs_for(source: str) -> list[str]:
    return [p.stem for p in sorted(VOCAB_DIR.glob("*.yaml"))
            if source in load_vocab(p.stem).sources]


def _strings(x: Any) -> Iterator[str]:
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for k, v in x.items():
            yield k
            yield from _strings(v)
    elif isinstance(x, list):
        for v in x:
            yield from _strings(v)


@pytest.mark.parametrize("root,source", CASES)
def test_no_group_in_two_splits(root: Path, source: str) -> None:
    seen: dict[str, str] = {}
    for d in iter_session_dicts(source, None, root):
        assert d["split"] in {"train", "val", "test"}
        assert seen.setdefault(d["group"], d["split"]) == d["split"], d["group"]
    assert seen
    if root == PROCESSED:
        saved = json.loads((root / source / "splits.json").read_text())["groups"]
        for g, sp in seen.items():
            assert saved[g] == sp, g
        df = pq.read_table(root / source / "calls.parquet",
                           columns=["group", "split"]).to_pandas()
        assert (df.groupby("group")["split"].nunique() == 1).all()


@pytest.mark.parametrize("root,source", CASES)
def test_every_tool_in_vocabulary(root: Path, source: str) -> None:
    classes = set(load_mapper(source).classes)
    shared = [load_vocab(v) for v in _vocabs_for(source)]
    used: set[str] = set()
    for d in iter_session_dicts(source, None, root):
        for b in d["batches"]:
            for c in b["calls"]:
                assert c["tool"] in classes, c["tool"]
                used.add(c["tool"])
    for v in shared:
        for cls in used:
            assert v.map(source, cls) in v.classes  # raises if a used class is unmapped


@pytest.mark.parametrize("root,source", CASES)
def test_no_long_strings_and_no_text_fields(root: Path, source: str) -> None:
    for d in iter_session_dicts(source, None, root):
        assert set(d) == SESSION_KEYS
        for b in d["batches"]:
            assert set(b) == BATCH_KEYS
            for c in b["calls"]:
                assert set(c) == CALL_KEYS
        for s in _strings(d):
            assert len(s) <= MAX_TEXT, f"{d['session_id']}: string of {len(s)} chars"
            assert TOKEN.match(s), f"{d['session_id']}: non-token string {s[:20]!r}"
    if root == PROCESSED:
        cols = set(pq.ParquetFile(root / source / "calls.parquet").schema_arrow.names)
        assert cols == PARQUET_COLS
        assert not any(TEXTY_KEY.search(c) for c in cols)


def test_vocab_files_cover_every_source_class() -> None:
    for p in sorted(VOCAB_DIR.glob("*.yaml")):
        v = load_vocab(p.stem)
        for src, m in v.sources.items():
            missing = set(load_mapper(src).classes) - set(m)
            assert not missing, f"{p.stem}: {src} classes {sorted(missing)} unmapped"


def test_checks_reject_tainted_data(tmp_path: Path) -> None:
    """The checks above must fail on a leaked group, a long string or a prompt-like field."""
    import gzip

    def write(sessions: list[dict[str, Any]]) -> None:
        with gzip.open(tmp_path / "tau_bench_sample.jsonl.gz", "wt") as f:
            for s in sessions:
                f.write(json.dumps(s) + "\n")

    def sess(sid: str, group: str, split: str, tool: str = "user_lookup",
             raw: str = "find_user_id_by_email", **extra: Any) -> dict[str, Any]:
        call = {"tool": tool, "service_ms": None, "error": False, "raw_tool": raw}
        return {"session_id": sid, "source": "tau_bench", "group": group, "has_timing": False,
                "trace_type": "real", "split": split,
                "batches": [{"batch_id": 0, "t_offset_ms": None, "node": None,
                             "calls": [call]}], **extra}

    write([sess("a", "g1", "train"), sess("b", "g1", "test")])
    with pytest.raises(AssertionError):
        test_no_group_in_two_splits(tmp_path, "tau_bench")
    write([sess("a", "g1", "train", tool="not_a_class")])
    with pytest.raises(AssertionError):
        test_every_tool_in_vocabulary(tmp_path, "tau_bench")
    for bad in (sess("a", "g1", "train", raw="x" * 65),
                sess("a", "g1", "train", raw="please refund my order"),
                sess("a", "g1", "train", prompt="hi")):
        write([bad])
        with pytest.raises(AssertionError):
            test_no_long_strings_and_no_text_fields(tmp_path, "tau_bench")
