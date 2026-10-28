from traces.schema import Batch, Call, Session

PRD_EXAMPLE = {
    "session_id": "sess_0091",
    "trace_type": "branching",
    "events": [
        {"t_offset_ms": 0, "node": "classify_intent", "tool_called": None},
        {"t_offset_ms": 120, "node": "lookup_order", "tool_called": "order_lookup"},
        {"t_offset_ms": 640, "node": "check_policy", "tool_called": "policy_check"},
        {"t_offset_ms": 1310, "node": "issue_refund", "tool_called": "refund_issue"},
        {"t_offset_ms": 1890, "node": "notify_user", "tool_called": None},
    ],
}


def make_session() -> Session:
    return Session(
        session_id="s1",
        source="tau_bench",
        group="task-7",
        has_timing=True,
        batches=[
            Batch(0, 0.0, None, [Call("order_lookup", 120.0, raw_tool="get_order_details")]),
            # Parallel batch: two calls emitted in one LLM turn.
            Batch(1, 900.0, "n1", [Call("refund_issue", 80.0), Call("policy_check", 40.0, True)]),
        ],
    )


def test_round_trip() -> None:
    s = make_session()
    assert Session.from_dict(s.to_dict()) == s


def test_parallel_batch() -> None:
    s = make_session()
    b = s.batches[1]
    assert b.is_parallel
    assert b.tools == frozenset({"refund_issue", "policy_check"})
    # Ties inside a batch are sorted by name.
    assert s.tool_sequence() == ["order_lookup", "policy_check", "refund_issue"]
    assert s.batch_sequence() == [frozenset({"order_lookup"}), b.tools]


def test_no_timing_is_none_not_zero() -> None:
    s = Session("s2", "bfcl", "q1", False, [Batch(0, None, calls=[Call("search")])])
    d = s.to_dict()
    assert d["has_timing"] is False
    assert d["batches"][0]["t_offset_ms"] is None
    assert d["batches"][0]["calls"][0]["service_ms"] is None
    assert Session.from_dict(d) == s


def test_prd_round_trip() -> None:
    s = Session.from_prd(PRD_EXAMPLE)
    assert s.has_timing and s.trace_type == "branching"
    assert s.tool_sequence() == ["order_lookup", "policy_check", "refund_issue"]
    assert s.batches[0].calls == []  # node-only event kept as an empty batch
    assert s.to_prd() == PRD_EXAMPLE


def test_prd_export_flattens_parallel_batch() -> None:
    events = make_session().to_prd()["events"]
    assert [e["tool_called"] for e in events] == ["order_lookup", "policy_check", "refund_issue"]
    assert events[1]["t_offset_ms"] == events[2]["t_offset_ms"] == 900.0
