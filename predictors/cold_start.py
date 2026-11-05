"""Cold-start times per tool class, read from the infra cold-start file by path.

    cs = ColdStarts.load(path)            # configs/cold_start.yaml (schema_version 1)
    cs.server("shell_other")              # -> "shell"
    cs.ms("shell_other", "cached", "median_ms")
    cs.tier("shell_other")                # light | medium | heavy
    cs.parallel_ms("shell", "cached")     # parallel_3 block (3 starts at once), None if absent

No cold-start number lives in code: everything comes from the file, which the infra session
generates from E0 (kind now, AWS later). ``label`` carries the file's environment and status
(e.g. "kind: lower bound; replace with AWS ...") so every result can say which values it used.
A class not listed under any server goes to the file's ``fallback`` server.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

MODES = ("cached", "uncached")
STATS = ("median_ms", "p95_ms")


@dataclass(frozen=True)
class ColdStarts:
    servers: dict[str, dict[str, Any]]
    class_server: dict[str, str]
    fallback: str
    environment: str
    status: str
    sha256: str
    path: str
    parallel: dict[str, dict[str, Any]] = field(default_factory=dict)  # parallel_3 block

    @classmethod
    def load(cls, path: Path | str) -> ColdStarts:
        p = Path(path)
        raw = p.read_bytes()
        d = yaml.safe_load(raw)
        if not isinstance(d, dict) or d.get("schema_version") != 1:
            raise ValueError(f"{p}: unsupported cold-start file")
        servers = d.get("servers") or {}
        fallback = str(d.get("fallback", ""))
        if fallback not in servers:
            raise ValueError(f"{p}: fallback server {fallback!r} not listed")
        class_server: dict[str, str] = {}
        for name, s in servers.items():
            for mode in MODES:
                for stat in STATS:
                    v = (s.get(mode) or {}).get(stat)
                    if not isinstance(v, int | float) or v < 0:
                        raise ValueError(f"{p}: {name}.{mode}.{stat} missing or negative")
            for c in s.get("classes") or []:
                class_server.setdefault(str(c), str(name))
        par = ((d.get("parallel_3") or {}).get("servers") or {})
        for name, s in par.items():
            for mode in MODES:
                for stat in STATS:
                    v = (s.get(mode) or {}).get(stat)
                    if not isinstance(v, int | float) or v < 0:
                        raise ValueError(f"{p}: parallel_3.{name}.{mode}.{stat} missing")
        return cls(servers, class_server, fallback, str(d.get("environment", "unknown")),
                   str(d.get("status", "")), hashlib.sha256(raw).hexdigest()[:12], str(p),
                   {str(k): v for k, v in par.items()})

    @property
    def label(self) -> str:
        return f"{self.environment}: {self.status}" if self.status else self.environment

    def server(self, tool_class: str) -> str:
        return self.class_server.get(tool_class, self.fallback)

    def ms(self, tool_class: str, mode: str = "cached", stat: str = "median_ms") -> float:
        if mode not in MODES or stat not in STATS:
            raise ValueError(f"mode must be in {MODES}, stat in {STATS}")
        return float(self.servers[self.server(tool_class)][mode][stat])

    def server_ms(self, server: str, mode: str = "cached", stat: str = "median_ms") -> float:
        return float(self.servers[server][mode][stat])

    def parallel_ms(self, server: str, mode: str = "cached", stat: str = "median_ms"
                    ) -> float | None:
        """Cold start of ``server`` when 3 tools start at once (``parallel_3``), if measured."""
        v = self.parallel.get(server)
        return None if v is None else float(v[mode][stat])

    def tier(self, tool_class: str) -> str:
        return str(self.servers[self.server(tool_class)].get("tier", "heavy"))

    def server_tier(self, server: str) -> str:
        return str(self.servers[server].get("tier", "heavy"))
