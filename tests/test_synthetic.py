"""Synthetic PRD trace generator (traces/generate_synthetic.py)."""

from __future__ import annotations

import gzip
from collections import Counter
from pathlib import Path

import pytest

from traces import generate_synthetic as gen
from traces.load import SAMPLES, load_sessions


def test_graphs_are_valid_and_follow_the_prd() -> None:
    g = gen.load_graphs()
    assert set(g) == set(gen.TYPES)
    a = g["branching"]["nodes"]
    assert a["lookup_order"]["next"] == {"check_policy": 0.70, "issue_refund": 0.20,
                                         "notify_user": 0.10}
    assert a["check_policy"]["next"] == {"issue_refund": 0.55, "notify_user": 0.45}
    b = g["deterministic"]["nodes"]
    assert [b[n]["tool"] for n in ("read_file", "run_tests", "format_diff", "post_comment")] \
        == ["read_file", "run_tests", "format_diff", "post_comment"]
    assert all(len(spec["next"]) == 1 for spec in b.values())


def test_sessions_follow_graph_and_carry_nodes() -> None:
    graphs = gen.load_graphs()
    for kind in gen.TYPES:
        nodes = graphs[kind]["nodes"]
        for s in gen.sessions(kind, 50, seed=0, root=SAMPLES):
            assert s.has_timing and s.trace_type == f"synthetic_{kind}"
            path = [b.node for b in s.batches]
            assert all(n in nodes for n in path)
            for x, y in zip(path, path[1:], strict=False):
                assert nodes[x]["next"].get(y, 0) > 0, (kind, x, y)
            ts = [b.t_offset_ms for b in s.batches]
            assert ts == sorted(ts) and ts[0] == 0.0
            for b in s.batches:
                assert [c.tool for c in b.calls] == ([nodes[b.node]["tool"]]
                                                     if nodes[b.node]["tool"] else [])


def test_branching_matches_prd_rates() -> None:
    n = 4000
    seqs = [s.tool_sequence() for s in gen.sessions("branching", n, seed=0, root=SAMPLES)]
    first_two = Counter(tuple(s[:2]) for s in seqs)
    assert abs(first_two[("order_lookup", "policy_check")] / n - 0.70) < 0.03
    assert abs(first_two[("order_lookup", "refund_issue")] / n - 0.20) < 0.03
    assert abs(first_two[("order_lookup",)] / n - 0.10) < 0.02


def test_same_seed_same_files(tmp_path: Path) -> None:
    outs = []
    for name in ("a", "b"):
        gen.generate("random_walk", n=40, seed=0, root=SAMPLES, out_root=tmp_path / name)
        d = tmp_path / name / "synthetic_random_walk"
        outs.append((gzip.decompress((d / "sessions.jsonl.gz").read_bytes()),
                     (d / "splits.json").read_bytes()))
    assert outs[0] == outs[1]
    other = list(gen.sessions("random_walk", 40, seed=1, root=SAMPLES))
    assert [s.tool_sequence() for s in other] != [
        s.tool_sequence() for s in load_sessions("synthetic_random_walk", None,
                                                 root=tmp_path / "a")]


def test_bad_graph_is_rejected(tmp_path: Path) -> None:
    p = tmp_path / "g.yaml"
    p.write_text("scenarios:\n  x:\n    start: a\n    nodes:\n"
                 "      a: {tool: t, next: {a: 0.5}}\n")
    with pytest.raises(ValueError):
        gen.load_graphs(p)
