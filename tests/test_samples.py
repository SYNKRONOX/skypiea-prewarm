"""Checks on the committed samples in data/samples/ (the only data available in CI)."""

import gzip
import json
from pathlib import Path

import pytest

from predictors.evaluate import group_split, topk_accuracy
from predictors.frequency import FrequencyPredictor
from predictors.markov import MarkovPredictor
from traces.schema import Session

SAMPLES = Path(__file__).resolve().parent.parent / "data" / "samples"
SESSION_SAMPLES = ["tracelab", "copilot_2026", "tau_bench", "tau2_bench", "bfcl"]
MAX_BYTES = 5 * 1024 * 1024


def load(name: str) -> list[dict]:
    with gzip.open(SAMPLES / f"{name}_sample.jsonl.gz", "rt", encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def sessions(name: str) -> list[Session]:
    return [Session.from_dict(d) for d in load(name)]


def test_all_samples_under_5mb() -> None:
    files = list(SAMPLES.glob("*.jsonl.gz"))
    assert files
    for f in files:
        assert f.stat().st_size < MAX_BYTES, f.name


@pytest.mark.parametrize("name", SESSION_SAMPLES)
def test_sample_is_schema_only(name: str) -> None:
    recs = load(name)
    assert recs
    for d in recs:
        # Only schema fields: no prompt, message or argument text can hide in extra keys.
        assert set(d) == {"session_id", "source", "group", "batches"}
        for b in d["batches"]:
            assert set(b) == {"batch_id", "t_emit_ms", "calls", "node"}
            for c in b["calls"]:
                assert set(c) == {"tool", "service_ms", "error"}
        assert Session.from_dict(d).to_dict() == d


@pytest.mark.parametrize("name", ["tracelab", "copilot_2026", "bfcl"])
def test_parallel_batches_present(name: str) -> None:
    assert any(b.is_parallel for s in sessions(name) for b in s.batches)


@pytest.mark.parametrize("name", ["tau2_bench", "copilot_2026"])
def test_markov_beats_frequency_on_held_out_groups(name: str) -> None:
    train, test = group_split(sessions(name), test_frac=0.3, seed=0)
    tr = [s.tool_sequence() for s in train]
    te = [s.tool_sequence() for s in test]
    freq = topk_accuracy(FrequencyPredictor().fit(tr), te, ks=(3,))[3]
    markov = topk_accuracy(MarkovPredictor(order=1).fit(tr), te, ks=(3,))[3]
    assert markov > freq


def test_huawei_sample_is_numeric() -> None:
    for row in load("huawei_coldstart"):
        assert row["totalCost_cold_start"] >= 0
        assert all(isinstance(v, float) for k, v in row.items() if k != "pool")
