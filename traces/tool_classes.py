"""Map raw tool names to a small set of tool classes, from configs/tool_classes/<source>.yaml.

YAML layout::

    classes: [read_file, write_file, ..., other]   # at most 15, must include the default
    default: other
    extends: tau_bench                             # optional: reuse another source's rules
    exact:  {read_file: [Read, get_file]}          # checked first
    prefix: {planning: [Task]}                     # then name prefixes
    regex:  {custom_tool: ['^custom_tool_\\d+$']}  # then regular expressions (re.search)
    ignore_case: false                             # optional, applies to regex rules

Anything that matches no rule maps to ``default``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent / "configs" / "tool_classes"
MAX_CLASSES = 15


@dataclass
class ToolMapper:
    classes: list[str]
    default: str
    exact: dict[str, str] = field(default_factory=dict)
    prefix: list[tuple[str, str]] = field(default_factory=list)
    regex: list[tuple[re.Pattern[str], str]] = field(default_factory=list)

    def __call__(self, raw: str) -> str:
        return self.map(raw)

    @lru_cache(maxsize=65536)  # noqa: B019  (mappers live for the whole run)
    def map(self, raw: str) -> str:
        if raw in self.exact:
            return self.exact[raw]
        for p, cls in self.prefix:
            if raw.startswith(p):
                return cls
        for rx, cls in self.regex:
            if rx.search(raw):
                return cls
        return self.default

    def __hash__(self) -> int:
        return id(self)


def _read(source: str) -> dict[str, Any]:
    path = CONFIG_DIR / f"{source}.yaml"
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    if base := cfg.get("extends"):
        merged = _read(base)
        for key in ("exact", "prefix", "regex"):
            merged.setdefault(key, {})
            for cls, items in (cfg.get(key) or {}).items():
                merged[key].setdefault(cls, []).extend(items)
        for key in ("classes", "default"):
            if key in cfg:
                merged[key] = cfg[key]
        return merged
    return cfg


@dataclass(frozen=True)
class ShellSplit:
    """Classify a shell call by the first word of its command (the ``shell_split`` block).

    Only the executable name is looked at; arguments are never seen or stored.
    """

    tools: frozenset[str]
    skip: frozenset[str]
    words: dict[str, str]
    default: str

    def classify(self, executables: list[str] | None) -> str:
        for word in executables or []:
            if word in self.skip:
                continue
            return self.words.get(word, self.default)
        return self.default


def load_shell_split(source: str) -> ShellSplit | None:
    """The source's first-word shell rules, or None if it has none."""
    cfg = _read(source).get("shell_split")
    if not cfg:
        return None
    words: dict[str, str] = {}
    for cls, items in cfg["words"].items():
        for w in items:
            if words.setdefault(str(w), cls) != cls:
                raise ValueError(f"{source}: shell word {w!r} mapped to two classes")
    return ShellSplit(frozenset(cfg["tools"]), frozenset(str(s) for s in cfg.get("skip", [])),
                      words, cfg.get("default", "shell_other"))


def load_mapper(source: str) -> ToolMapper:
    """Load and check the mapping for ``source``."""
    cfg = _read(source)
    classes, default = list(cfg["classes"]), cfg.get("default", "other")
    if len(classes) > MAX_CLASSES:
        raise ValueError(f"{source}: {len(classes)} classes, max is {MAX_CLASSES}")
    if default not in classes:
        raise ValueError(f"{source}: default class {default!r} not in classes")
    m = ToolMapper(classes, default)
    flags = re.IGNORECASE if cfg.get("ignore_case") else 0
    for key in ("exact", "prefix", "regex"):
        for cls, items in (cfg.get(key) or {}).items():
            if cls not in classes:
                raise ValueError(f"{source}: {key} rule maps to unknown class {cls!r}")
            for item in items:
                if key == "exact":
                    if item in m.exact and m.exact[item] != cls:
                        raise ValueError(f"{source}: {item!r} mapped to two classes")
                    m.exact[item] = cls
                elif key == "prefix":
                    m.prefix.append((item, cls))
                else:
                    m.regex.append((re.compile(item, flags), cls))
    return m
