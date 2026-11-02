"""Shared-vocabulary loading and mapping (traces/vocab.py, configs/vocab/)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from traces import vocab as vocab_mod
from traces.load import SAMPLES, load_batches, load_calls_df, load_sequences
from traces.vocab import load_vocab


def test_coding_common_maps_both_sources() -> None:
    v = load_vocab("coding_common")
    assert set(v.sources) == {"tracelab", "copilot_2026"}
    assert v.map("tracelab", "git") == "shell"
    assert v.map("tracelab", "shell_io") == "shell"
    assert v.map("copilot_2026", "project") == "search"
    assert v.map("copilot_2026", "read_file") == "read_file"
    assert len(v.classes) == len(set(v.classes))


def test_unmapped_class_or_source_raises() -> None:
    v = load_vocab("coding_common")
    with pytest.raises(KeyError):
        v.map("tracelab", "no_such_class")
    with pytest.raises(KeyError):
        v.map("tau_bench", "user_lookup")


def test_mapping_comes_from_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "toy.yaml").write_text(yaml.safe_dump(
        {"name": "toy", "classes": ["a", "b"], "sources": {"s": {"x": "a", "y": "b"}}}))
    monkeypatch.setattr(vocab_mod, "VOCAB_DIR", tmp_path)
    load_vocab.cache_clear()
    try:
        v = load_vocab("toy")
        assert v.map("s", "y") == "b"
        (tmp_path / "bad.yaml").write_text(yaml.safe_dump(
            {"classes": ["a"], "sources": {"s": {"x": "zzz"}}}))
        with pytest.raises(ValueError):
            load_vocab("bad")
    finally:
        load_vocab.cache_clear()


@pytest.mark.parametrize("source", ["tracelab", "copilot_2026"])
def test_loader_applies_vocab(source: str) -> None:
    classes = set(load_vocab("coding_common").classes)
    seqs = load_sequences(source, None, root=SAMPLES, vocab="coding_common")
    assert seqs and all(t in classes for s in seqs for t in s)
    native = load_sequences(source, None, root=SAMPLES)
    assert [len(s) for s in seqs] == [len(s) for s in native]
    batches = load_batches(source, None, root=SAMPLES, vocab="coding_common")
    assert all(t in classes for s in batches for b in s for t in b)
    df = load_calls_df(source, None, root=SAMPLES, vocab="coding_common")
    assert set(df["tool"]) <= classes


def test_vocab_rejects_raw_level() -> None:
    with pytest.raises(ValueError):
        load_sequences("tracelab", None, root=SAMPLES, level="raw", vocab="coding_common")
