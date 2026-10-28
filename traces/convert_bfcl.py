"""BFCL v4 parallel and parallel_multiple converter.

Each item's expected calls (``possible_answer_*.json`` ``ground_truth``) form ONE batch, so
every session has exactly one parallel batch. The same function may appear several times
in a batch (one call per copy). There is no timing and no error flag. group = item id.
Question text and arguments are not read.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from traces.schema import Batch, Call, Session


def convert(raw: Path, stats: Counter[str], **opts: Any) -> Iterator[Session]:
    files = sorted((raw / "bfcl").glob("possible_answer_*.json"))
    if not files:
        raise FileNotFoundError("no bfcl answer files; run data/download.py first")
    for path in files:
        with path.open(encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                d = json.loads(line)
                calls = [Call(name, None, False, name) for g in d["ground_truth"] for name in g]
                yield Session(d["id"], "bfcl", d["id"], False, [Batch(0, None, None, calls)])
