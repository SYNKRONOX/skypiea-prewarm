"""Toolathlon converter stub: the dataset is gated and is not downloaded."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from traces.schema import Session


def convert(raw: Path, stats: Counter[str], **opts: Any) -> Iterator[Session]:
    raise RuntimeError(
        "toolathlon: gated dataset, not downloaded. "
        "See `python data/download.py --dataset toolathlon` for access steps."
    )
    yield  # pragma: no cover  (makes this a generator)
