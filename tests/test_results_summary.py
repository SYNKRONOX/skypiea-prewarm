"""sim/results_summary.py: session-level bootstrap CI and the replayer parser."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from sim import results_summary as rs


def _calls(cold_by_session: dict[str, list[bool]]) -> pd.DataFrame:
    return pd.DataFrame([{"session": s, "cold_b": c} for s, cs in cold_by_session.items()
                         for c in cs])


def test_session_ci_resamples_sessions() -> None:
    same = _calls({f"s{i}": [True, False] for i in range(20)})
    assert rs.session_ci(same) == (0.5, 0.5)  # every session is the same: no spread
    mixed = _calls({**{f"c{i}": [True] * 4 for i in range(5)},
                    **{f"w{i}": [False] * 4 for i in range(15)}})
    lo, hi = rs.session_ci(mixed)
    assert lo < 0.25 < hi and rs.session_ci(mixed) == (lo, hi)  # seeded


def test_scenarios_table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rs, "committed_elsewhere", lambda p: "abc1234")
    rows = [{"dataset": "real", "scenario": "A", "policy": pol, "session": f"s{i}",
             "cold": str(i < k), "added_ms": 0.0, "log": "A.log"}
            for pol, k in (("keep_alive", 2), ("predictive", 1)) for i in range(4)]
    pd.DataFrame(rows).to_csv(tmp_path / "x_calls.csv", index=False)
    pd.DataFrame([{"dataset": "real", "scenario": "A", "policy": "keep_alive", "log": "A.log",
                   "cold": 2, "idle_pod_s": 12.5, "added_ms_p50": 0, "added_ms_p99": 9,
                   "error": ""}]).to_csv(tmp_path / "x_summary.csv", index=False)
    out = "\n".join(rs.scenarios(str(tmp_path / "*_calls.csv")))
    assert "real A: 4 sessions" in out and "Mismatch" not in out
    assert "| real | A | keep_alive | 4 | 4 | 50.0 [" in out and "| 12 | n/a" in out
    assert "| real | A | predictive | 4 | 4 | 25.0 [" in out
    assert rs.TODO in "\n".join(rs.scenarios(None))
