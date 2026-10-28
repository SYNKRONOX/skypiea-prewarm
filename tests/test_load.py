"""Smoke tests for the loader API, run against the committed samples."""

import pytest

from predictors.evaluate import topk_accuracy
from predictors.frequency import FrequencyPredictor
from predictors.markov import MarkovPredictor
from traces.load import (
    SAMPLES,
    load_all,
    load_batches,
    load_calls_df,
    load_sequences,
    load_sessions,
)


@pytest.mark.parametrize("source", ["tracelab", "copilot_2026", "tau_bench", "tau2_bench", "bfcl"])
def test_loaders_agree(source: str) -> None:
    sessions = load_sessions(source, "train", root=SAMPLES)
    assert sessions
    seqs = load_sequences(source, "train", root=SAMPLES)
    batches = load_batches(source, "train", root=SAMPLES)
    df = load_calls_df(source, "train", root=SAMPLES)
    assert len(seqs) == len(batches) == len(sessions)
    assert sum(len(s) for s in seqs) == len(df)
    assert set(df["split"]) == {"train"}
    assert seqs[0] == sessions[0].tool_sequence()
    assert batches[0] == sessions[0].batch_sequence()


def test_split_filtering_and_raw_level() -> None:
    every = load_sessions("tau_bench", None, root=SAMPLES)
    parts = sum(len(load_sessions("tau_bench", s, root=SAMPLES)) for s in ("train", "val", "test"))
    assert len(every) == parts
    raw = load_sequences("tau_bench", "train", root=SAMPLES, level="raw")
    assert any(t.startswith("get_") for seq in raw for t in seq)


def test_load_all_combines_sources() -> None:
    both = load_all(["tau_bench", "bfcl"], "test", root=SAMPLES)
    assert {s.source for s in both} == {"tau_bench", "bfcl"}


def test_markov_beats_frequency_on_held_out_sample() -> None:
    train = load_sequences("tau_bench", "train", root=SAMPLES)
    test = load_sequences("tau_bench", "test", root=SAMPLES)
    freq = topk_accuracy(FrequencyPredictor().fit(train), test, ks=(1,))[1]
    markov = topk_accuracy(MarkovPredictor(order=1).fit(train), test, ks=(1,))[1]
    assert markov > freq
