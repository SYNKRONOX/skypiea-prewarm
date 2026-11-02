"""Shared vocabularies that map several sources' tool classes onto one set of classes.

YAML layout (configs/vocab/<name>.yaml)::

    name: coding_common
    classes: [read_file, write_file, ...]
    sources:
      tracelab:      {shell_other: shell, git: shell, ...}
      copilot_2026:  {project: search, ...}

Every class a source emits must be mapped; an unmapped class raises instead of silently
becoming ``other``.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

VOCAB_DIR = Path(__file__).resolve().parent.parent / "configs" / "vocab"


@dataclass(frozen=True)
class Vocab:
    name: str
    classes: tuple[str, ...]
    sources: dict[str, dict[str, str]]

    def mapping(self, source: str) -> dict[str, str]:
        if source not in self.sources:
            raise KeyError(f"vocabulary {self.name!r} has no mapping for source {source!r}")
        return self.sources[source]

    def map(self, source: str, cls: str) -> str:
        m = self.mapping(source)
        if cls not in m:
            raise KeyError(f"vocabulary {self.name!r}: {source} class {cls!r} is not mapped")
        return m[cls]


@lru_cache(maxsize=16)
def load_vocab(name: str) -> Vocab:
    cfg = yaml.safe_load((VOCAB_DIR / f"{name}.yaml").read_text(encoding="utf-8"))
    classes = tuple(cfg["classes"])
    sources = {src: dict(m) for src, m in cfg["sources"].items()}
    for src, m in sources.items():
        bad = sorted(set(m.values()) - set(classes))
        if bad:
            raise ValueError(f"vocabulary {name!r}: {src} maps to unknown classes {bad}")
    return Vocab(cfg.get("name", name), classes, sources)
