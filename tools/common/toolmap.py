"""Load tools/tool_map.yaml and resolve a tool class to its server."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

TOOL_MAP_PATH = Path(__file__).resolve().parent.parent / "tool_map.yaml"
TIERS = ("light", "medium", "heavy")


@dataclass(frozen=True)
class ServerSpec:
    name: str
    tier: str
    work: str
    classes: tuple[str, ...]


@dataclass(frozen=True)
class ToolMap:
    servers: dict[str, ServerSpec]
    fallback: str
    fallback_patterns: tuple[re.Pattern[str], ...]

    def server_for(self, cls: str) -> str | None:
        """Server for a class, the fallback for other_* classes, None if unmapped."""
        for spec in self.servers.values():
            if cls in spec.classes:
                return spec.name
        if any(p.search(cls) for p in self.fallback_patterns):
            return self.fallback
        return None


def parse(cfg: dict) -> ToolMap:
    servers: dict[str, ServerSpec] = {}
    seen: dict[str, str] = {}
    for name, s in cfg["servers"].items():
        if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
            raise ValueError(f"bad server name {name!r}")
        if s["tier"] not in TIERS:
            raise ValueError(f"{name}: tier must be one of {TIERS}")
        if s.get("work", "cpu") not in ("cpu", "io"):
            raise ValueError(f"{name}: work must be cpu or io")
        for c in s.get("classes", []):
            if c in seen:
                raise ValueError(f"class {c!r} mapped to both {seen[c]} and {name}")
            seen[c] = name
        servers[name] = ServerSpec(name, s["tier"], s.get("work", "cpu"),
                                   tuple(s.get("classes", [])))
    if cfg["fallback"] not in servers:
        raise ValueError("fallback must be one of the servers")
    pats = tuple(re.compile(p) for p in cfg.get("fallback_patterns", []))
    return ToolMap(servers, cfg["fallback"], pats)


@lru_cache(maxsize=1)
def load(path: str | Path = TOOL_MAP_PATH) -> ToolMap:
    return parse(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


def k8s_name(server: str) -> str:
    """Kubernetes object name for a server (DNS-1123: underscores -> dashes)."""
    return server.replace("_", "-")
