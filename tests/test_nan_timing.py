"""Unknown or non-finite tool run times (TraceLab has 712 calls with no service_ms).

They enter as None (JSON null) or NaN (calls.parquet). Every consumer must treat them as
unknown, never let a NaN through: graph gaps and quantiles, ranker features, the
simulator's batch ends and waits, and the hideable fractions.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from predictors.cold_start import ColdStarts
from predictors.data import episodes, to_episode
from predictors.decide import DecideConfig, load_pricing
from predictors.graph import TransitionGraph, _think_gaps
from sim.offline import Env, Spec, Stats, batch_times, hideable_rows, load_config, simulate
from tests.test_decide import _cold_file
from traces.schema import Batch, Call, Session


def _sess(sid: str, bad: float | None) -> Session:
    return Session(sid, "toy", sid, True, [
        Batch(0, 1000.0, None, [Call("read_file", 100.0, False, "read_file")]),
        Batch(1, 5000.0, None, [Call("shell", bad, False, "shell"),
                                Call("read_file", 50.0, False, "read_file")]),
        Batch(2, 9000.0, None, [Call("shell", bad, False, "shell")]),
        Batch(3, 15000.0, None, [Call("read_file", 80.0, False, "read_file")]),
    ])


def test_non_finite_service_times_become_unknown_everywhere(tmp_path: Path) -> None:
    for bad in (None, math.nan, math.inf):
        ep = to_episode(_sess("s", bad))
        assert [e.service_ms for e in ep.events] == [100.0, 50.0, None, None, 80.0]
        # a batch end ignores the unknown call; an all-unknown batch ends at its start
        assert batch_times(ep) == [(1000.0, 1100.0), (5000.0, 5050.0), (9000.0, 9000.0),
                                   (15000.0, 15080.0)]
        assert all(math.isfinite(g) for _, _, g in _think_gaps(ep))

    g = TransitionGraph(min_support=1).fit(episodes([_sess(f"s{i}", math.nan)
                                                     for i in range(4)]))
    json.dumps(g.to_dict(), allow_nan=False)  # no NaN in the exported quantiles

    env = Env(ColdStarts.load(_cold_file(tmp_path / "cold.yaml")), "cached", load_config(),
              load_pricing(), DecideConfig.load())
    ep = to_episode(_sess("s", math.nan))
    for spec in (Spec("keep_alive_30", 30_000.0), Spec("oracle", 0.0, oracle=True)):
        st = Stats()
        simulate(ep, spec, env, ["generic", "read", "shell"], st)
        assert st.calls == 5 and all(math.isfinite(w) for w in st.added)
    rows = hideable_rows([ep], env, {})
    assert all(math.isfinite(float(r["hideable_pct"])) for r in rows)
