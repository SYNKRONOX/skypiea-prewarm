"""Checks on the committed samples in data/samples/ (the only data available in CI)."""

import gzip
import json
from pathlib import Path

import pytest

from traces.load import SAMPLES
from traces.schema import Session
from traces.tool_classes import load_mapper

SESSION_SAMPLES = ["tracelab", "copilot_2026", "tau_bench", "tau2_bench", "bfcl"]
MAX_BYTES = 5 * 1024 * 1024
MAX_TEXT = 64


def load(name: str) -> list[dict]:
    with gzip.open(Path(SAMPLES) / f"{name}_sample.jsonl.gz", "rt", encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def test_all_samples_under_5mb() -> None:
    files = list(Path(SAMPLES).glob("*.jsonl.gz"))
    assert files
    for f in files:
        assert f.stat().st_size < MAX_BYTES, f.name


@pytest.mark.parametrize("name", SESSION_SAMPLES)
def test_sample_is_schema_only(name: str) -> None:
    recs = load(name)
    assert recs
    classes = set(load_mapper(name).classes)
    for d in recs:
        # Only schema fields: no prompt, message or argument text can hide in extra keys.
        assert set(d) == {"session_id", "source", "group", "has_timing", "trace_type",
                          "batches", "split"}
        assert d["split"] in {"train", "val", "test"}
        for b in d["batches"]:
            assert set(b) == {"batch_id", "t_offset_ms", "node", "calls"}
            assert (b["t_offset_ms"] is not None) == d["has_timing"]
            for c in b["calls"]:
                assert set(c) == {"tool", "service_ms", "error", "raw_tool"}
                assert c["tool"] in classes
                assert len(c["raw_tool"]) <= MAX_TEXT
        split = d.pop("split")
        assert split
        assert Session.from_dict(d).to_dict() == d


@pytest.mark.parametrize("name", SESSION_SAMPLES)
def test_groups_do_not_cross_splits(name: str) -> None:
    seen: dict[str, str] = {}
    for d in load(name):
        assert seen.setdefault(d["group"], d["split"]) == d["split"]


@pytest.mark.parametrize("name", ["tracelab", "copilot_2026", "bfcl"])
def test_parallel_batches_present(name: str) -> None:
    assert any(len(b["calls"]) > 1 for d in load(name) for b in d["batches"])


def test_huawei_sample_is_numeric() -> None:
    for row in load("huawei_coldstart"):
        assert row["totalCost_cold_start"] >= 0
        assert all(isinstance(v, float) for k, v in row.items() if k != "pool")
