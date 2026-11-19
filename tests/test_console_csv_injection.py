"""Issue #6: CSV exports neutralise cells that a spreadsheet would run as a formula."""

from __future__ import annotations

import csv
import io

from console.engine import to_csv


def test_formula_cells_are_neutralised():
    rows = [{"a": "=cmd|' /C calc'!A0", "b": "+1+1", "c": "-2+3", "d": "@SUM(A1)",
             "e": "\t=1", "f": "plain", "g": -1.5, "h": 3}]
    out = list(csv.DictReader(io.StringIO(to_csv(rows, list("abcdefgh")))))[0]
    assert out["a"] == "'=cmd|' /C calc'!A0"
    assert out["b"] == "'+1+1" and out["c"] == "'-2+3" and out["d"] == "'@SUM(A1)"
    assert out["e"] == "'\t=1"
    assert out["f"] == "plain" and out["g"] == "-1.5" and out["h"] == "3"   # numbers kept
