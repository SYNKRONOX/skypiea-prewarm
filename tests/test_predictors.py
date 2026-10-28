import pytest

from predictors.base import ThresholdPolicy
from predictors.evaluate import group_split, topk_accuracy
from predictors.frequency import FrequencyPredictor
from predictors.markov import MarkovPredictor
from traces.schema import Batch, Call, Session

SEQS = (
    7 * [["order_lookup", "policy_check", "refund_issue"]]
    + 2 * [["order_lookup", "refund_issue"]]
    + 1 * [["order_lookup"]]
)


@pytest.fixture
def markov1() -> MarkovPredictor:
    return MarkovPredictor(order=1).fit(SEQS)


def test_markov_transition_probs(markov1: MarkovPredictor) -> None:
    p = markov1.predict_proba(["order_lookup"])
    assert p["policy_check"] == pytest.approx(0.7)
    assert p["refund_issue"] == pytest.approx(0.2)


def test_threshold_policy_branches(markov1: MarkovPredictor) -> None:
    low = ThresholdPolicy(markov1, tau=0.15).decide(["order_lookup"])
    assert set(low) == {"policy_check", "refund_issue"}
    high = ThresholdPolicy(markov1, tau=0.6).decide(["order_lookup"])
    assert set(high) == {"policy_check"}


def test_markov2_beats_frequency() -> None:
    freq = FrequencyPredictor().fit(SEQS)
    m2 = MarkovPredictor(order=2).fit(SEQS)
    assert topk_accuracy(m2, SEQS, ks=(1,))[1] >= topk_accuracy(freq, SEQS, ks=(1,))[1]


def test_k_step_lookahead(markov1: MarkovPredictor) -> None:
    one = markov1.predict_proba(["order_lookup"])["refund_issue"]
    two = markov1.k_step(["order_lookup"], k=2)["refund_issue"]
    assert one == pytest.approx(0.2)
    assert two > 0.2


def test_backoff_to_unigram() -> None:
    m = MarkovPredictor(order=2).fit(SEQS)
    assert m.predict_proba(["never_seen"]) == m.unigram


def test_group_split_no_leak() -> None:
    sessions = [
        Session(f"s{i}", "test", f"g{i % 5}", batches=[Batch(0, None, calls=[Call("a")])])
        for i in range(20)
    ]
    train, test = group_split(sessions, test_frac=0.4, seed=1)
    assert len(train) + len(test) == 20
    assert not {s.group for s in train} & {s.group for s in test}
