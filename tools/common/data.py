"""Bundled fake data: support JSON tables and the sample code repository."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DATA_DIR = Path(os.environ.get("SKYPIEA_DATA", Path(__file__).resolve().parent / "data"))
SUPPORT_DIR = DATA_DIR / "support"
SAMPLE_REPO = DATA_DIR / "sample_repo"


def load_json(name: str) -> list[dict[str, Any]]:
    return json.loads((SUPPORT_DIR / f"{name}.json").read_text(encoding="utf-8"))


def repo_path(rel: str) -> Path:
    """Resolve a path inside the sample repo; refuse anything that escapes it."""
    p = (SAMPLE_REPO / rel.lstrip("/")).resolve()
    if p != SAMPLE_REPO.resolve() and SAMPLE_REPO.resolve() not in p.parents:
        raise ValueError(f"path outside sample repo: {rel}")
    return p


def repo_files() -> list[str]:
    root = SAMPLE_REPO.resolve()
    return sorted(str(p.relative_to(root)) for p in root.rglob("*")
                  if p.is_file() and "__pycache__" not in p.parts)
