from traces.schema import Batch, Call, Session


def make_session() -> Session:
    return Session(
        session_id="s1",
        source="tau-bench",
        group="task-7",
        batches=[
            Batch(0, 0.0, [Call("order_lookup", 120.0)]),
            # Parallel batch: two calls emitted in one LLM turn.
            Batch(1, 900.0, [Call("refund_issue", 80.0), Call("policy_check", 40.0, True)], "n1"),
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
