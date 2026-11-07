"""FR-6 reload check (W1 P0 #3): real bundles, in-process service, requests in flight."""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
pytest.importorskip("uvicorn")


@pytest.mark.skipif(not (ROOT / "models" / "lgbm-coding-af00023").is_dir(),
                    reason="real bundles not present (make fetch-models)")
def test_reload_under_load_is_fast_and_serves_in_flight_requests() -> None:
    from services.predictor.reload_check import run
    res = run(ROOT / "models", ROOT / "configs" / "cold_start.yaml", threads=2, settle_s=0.3)
    assert res["all_reloads_ok"] and res["non_200"] == 0
    assert res["every_in_flight_request_served"]
    for r in res["reloads"]:
        assert r["spanning_request"]["started_before_reload"]
        assert r["spanning_request"]["ended_after_reload"]
        assert r["service_seconds"] < 2.0            # the FR-6 budget (laptop CPU)
