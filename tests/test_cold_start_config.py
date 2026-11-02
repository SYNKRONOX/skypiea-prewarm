"""configs/cold_start.yaml: schema check, and it must equal what the generator derives from the
committed E0 runs CSVs (so no number in it was edited by hand)."""

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
CFG = ROOT / "configs" / "cold_start.yaml"
PHASES = ["scheduling", "image_pull", "container_start", "app_init", "readiness_lag",
          "first_call"]


def load() -> dict:
    return yaml.safe_load(CFG.read_text())


def test_schema():
    doc = load()
    servers = yaml.safe_load((ROOT / "tools" / "tool_map.yaml").read_text())["servers"]
    assert doc["schema_version"] == 1 and doc["environment"] in ("kind", "aws")
    assert doc["units"] == "ms" and doc["fallback"] in doc["servers"]
    assert set(doc["servers"]) == set(servers)
    for name, s in doc["servers"].items():
        assert s["classes"] == servers[name].get("classes", [])
        for mode in ("cached", "uncached"):
            m = s[mode]
            assert m["n"] > 0 and 0 < m["median_ms"] <= m["p95_ms"]
            assert set(m["phases_median_ms"]) == set(PHASES)
        assert s["cached"]["phases_median_ms"]["image_pull"] == 0
        assert s["warm_call_median_ms"] > 0
    if doc["environment"] == "kind":
        assert "lower bound" in doc["status"]


def test_matches_generator():
    pytest.importorskip("pandas")
    from experiments.e0_write_cold_start import build

    doc = load()
    assert build(doc["environment"]) == doc
