"""End-to-end pipeline test on a tiny synthetic BFCL tree, plus the cleaning and split units."""

import json
from collections import Counter
from pathlib import Path

import pandas as pd
import pytest

from traces import pipeline
from traces.load import load_calls_df, load_sessions
from traces.schema import Batch, Call, Session
from traces.tool_classes import load_mapper


def fake_bfcl(raw: Path, n: int = 30) -> None:
    (raw / "bfcl").mkdir(parents=True)
    with (raw / "bfcl" / "possible_answer_BFCL_v4_parallel.json").open("w") as f:
        for i in range(n):
            gt = [{"get_current_weather": {}}, {"math.factorial": {}}, {"get_current_weather": {}}]
            f.write(json.dumps({"id": f"parallel_{i}", "ground_truth": gt}) + "\n")


def test_pipeline_end_to_end(tmp_path: Path) -> None:
    raw, out = tmp_path / "raw", tmp_path / "processed"
    fake_bfcl(raw)
    report = pipeline.run("bfcl", raw=raw, out_root=out)
    assert "| Sessions | 30 |" in report
    assert "| Parallel batches (2+ calls) | 100.0% |" in report

    splits = json.loads((out / "bfcl" / "splits.json").read_text())["groups"]
    assert Counter(splits.values()) == {"train": 17, "val": 4, "test": 9}

    train = load_sessions("bfcl", "train", root=out)
    assert len(train) == 17
    assert train[0].batches[0].tools == frozenset({"weather", "math"})
    assert [c.raw_tool for c in train[0].batches[0].calls].count("get_current_weather") == 2

    df = load_calls_df("bfcl", None, root=out)
    assert len(df) == 90
    assert df["t_offset_ms"].isna().all()  # no timing is invented
    assert set(df.groupby("group")["split"].nunique()) == {1}


def test_validate_catches_group_in_two_splits(tmp_path: Path) -> None:
    raw, out = tmp_path / "raw", tmp_path / "processed"
    fake_bfcl(raw, n=10)
    pipeline.run("bfcl", raw=raw, out_root=out)
    path = out / "bfcl" / "splits.json"
    data = json.loads(path.read_text())
    g = next(iter(data["groups"]))
    data["groups"][g] = "test" if data["groups"][g] != "test" else "train"
    path.write_text(json.dumps(data))
    with pytest.raises(pipeline.ValidationError):
        pipeline.validate(out / "bfcl", load_mapper("bfcl"))


def test_clean_sorts_clips_and_drops() -> None:
    stats: Counter[str] = Counter()
    clean = pipeline.Clean(min_batches=2, stats=stats)
    s = Session("s", "x", "g", True, [
        Batch(0, 500.0, calls=[Call("a", -5.0)]),
        Batch(1, 100.0, calls=[Call("b", 9e9)]),
    ])
    out = clean(s)
    assert out is not None
    assert [b.t_offset_ms for b in out.batches] == [100.0, 500.0]
    assert [b.batch_id for b in out.batches] == [0, 1]
    assert out.batches[0].calls[0].service_ms == pipeline.MAX_SERVICE_MS
    assert out.batches[1].calls[0].service_ms == 0.0
    assert clean(s) is None  # duplicate id
    assert clean(Session("t", "x", "g", False, [Batch(0, None, calls=[Call("a")])])) is None
    assert clean(Session("u", "x", "g", False, [Batch(0, None)])) is None
    assert stats["dropped_duplicate_id"] == stats["dropped_fewer_than_2_batches"] == 1
    assert stats["dropped_empty"] == 1
    assert stats["clipped_negative_service"] == stats["clipped_service_over_1h"] == 1


def test_assign_splits_is_deterministic_and_proportional() -> None:
    groups = [f"g{i}" for i in range(100)]
    a, b = pipeline.assign_splits(groups), pipeline.assign_splits(list(reversed(groups)))
    assert a == b
    assert Counter(a.values()) == {"train": 56, "val": 14, "test": 30}


@pytest.mark.parametrize("source,raw,expected", [
    ("tracelab", "Read", "read_file"),
    ("tracelab", "TaskCreate", "planning"),
    ("tracelab", "custom_tool_25", "other"),
    ("tracelab", "Bash:git", "git"),
    ("tracelab", "exec_command:run_tests", "run_tests"),
    ("tracelab", "Bash:shell_other", "shell_other"),
    ("tracelab", "never_seen_tool", "other"),
    ("copilot_2026", "tool_d299a3895ad9", "custom_tool"),
    ("tau_bench", "get_order_details", "order_lookup"),
    ("tau2_bench", "get_reservation_details", "reservation_lookup"),
    ("bfcl", "fetch_DNA_sequence", "science"),
])
def test_tool_class_mapping(source: str, raw: str, expected: str) -> None:
    m = load_mapper(source)
    assert len(m.classes) <= 15
    assert m(raw) == expected


def test_parquet_columns_match_schema(tmp_path: Path) -> None:
    raw, out = tmp_path / "raw", tmp_path / "processed"
    fake_bfcl(raw, n=5)
    pipeline.run("bfcl", raw=raw, out_root=out)
    df = pd.read_parquet(out / "bfcl" / "calls.parquet")
    assert list(df.columns) == pipeline.CALL_SCHEMA.names


@pytest.mark.parametrize("executables,expected", [
    (["pytest"], "run_tests"),
    (["cd", "pytest"], "run_tests"),            # leading setup word is skipped
    (["git", "grep"], "git"),                   # only the first word counts
    (["rg", "head"], "search"),
    (["sed"], "read_file"),
    (["cargo"], "build"),
    (["pip"], "package"),
    (["docker"], "shell_other"),
    (["cd"], "shell_other"),                    # nothing after the setup word
    ([], "shell_other"),
    (None, "shell_other"),
])
def test_tracelab_shell_first_word(executables: list[str] | None, expected: str) -> None:
    from traces.tool_classes import load_shell_split
    split = load_shell_split("tracelab")
    assert split is not None
    assert split.classify(executables) == expected


def test_tracelab_converter_never_keeps_command_text() -> None:
    from traces.convert_tracelab import _slim_tool
    from traces.tool_classes import load_shell_split
    stats: Counter[str] = Counter()
    t = {"tool_call_id": "c1", "tool_name": "Bash", "emitted_at": "2026-01-01T00:00:00Z",
         "tool_wall_latency_ms": 5, "is_error": False, "executables": ["git", "SECRET"],
         "command_skeleton": "git | SECRET", "input_chars": 99}
    out = _slim_tool(t, load_shell_split("tracelab"), stats)
    assert out["shell_class"] == "git"
    assert "SECRET" not in json.dumps(out)
    assert set(out) == {"tool_call_id", "tool_name", "emitted_at", "tool_wall_latency_ms",
                        "is_error", "shell_class"}


def test_extend_splits_keeps_existing_and_is_stable() -> None:
    old = pipeline.assign_splits([f"g{i}" for i in range(50)])
    new_groups = [f"g{i}" for i in range(50)] + [f"n{i}" for i in range(2000)]
    a = pipeline.extend_splits(new_groups, old)
    assert all(a[g] == old[g] for g in old)
    assert a == pipeline.extend_splits(list(reversed(new_groups)), old)
    fresh = Counter(a[g] for g in new_groups if g.startswith("n"))
    assert abs(fresh["test"] / 2000 - 0.30) < 0.04
    assert abs(fresh["val"] / 2000 - 0.14) < 0.04
