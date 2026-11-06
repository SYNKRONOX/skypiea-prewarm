"""docs/training_data.md is generated from the split reports, never typed."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REPORTS = [ROOT / "data" / "processed" / s / "report.md"
           for s in ("tau2_bench", "tau_bench", "tracelab", "copilot_2026", "synthetic_prd")]


def test_committed_table_exists_and_names_its_generator():
    text = (ROOT / "docs" / "training_data.md").read_text()
    assert "tools/training_data_table.py" in text and "| support | tau2_bench |" in text


@pytest.mark.skipif(not all(p.exists() for p in REPORTS),
                    reason="data/processed not present (datasets are not committed)")
def test_table_matches_the_split_reports():
    r = subprocess.run([sys.executable, "tools/training_data_table.py", "--check"], cwd=ROOT,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
