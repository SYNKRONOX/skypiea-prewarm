"""Which LLMs the console can switch to (W1 #12).

- Local Ollama models that report the `tools` capability: GET {OLLAMA_URL}/api/tags, then
  POST /api/show per model (its "capabilities" list). Models without `tools` are listed as
  not selectable (the demo needs tool calling). qwen3 models run with thinking DISABLED
  (console/llm_stream.py sends think=false), and say so in their label.
- Optionally ONE hosted OpenAI-compatible endpoint: when AGENT_API_KEY is set and
  AGENT_BASE_URL is not the local Ollama, model AGENT_MODEL at AGENT_BASE_URL. The key is
  only passed to that endpoint as a Bearer token; it is never shown, logged or stored, and
  /api/models reports only whether it is set.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")


def _is_local_ollama(base_url: str) -> bool:
    b = base_url.rstrip("/")
    return b.endswith(":11434/v1") and ("localhost" in b or "127.0.0.1" in b)


async def list_models(http: httpx.AsyncClient, current: str | None = None,
                      ollama: str = OLLAMA_URL, env: dict[str, str] | None = None
                      ) -> list[dict[str, Any]]:
    env = dict(os.environ) if env is None else env
    out: list[dict[str, Any]] = []
    try:
        tags = (await http.get(f"{ollama}/api/tags", timeout=3)).json().get("models", [])
    except (httpx.HTTPError, ValueError):
        tags = []
    for m in sorted(tags, key=lambda x: x.get("name", "")):
        name = m.get("name")
        if not name:
            continue
        try:
            caps = (await http.post(f"{ollama}/api/show", json={"model": name}, timeout=5)
                    ).json().get("capabilities") or []
        except (httpx.HTTPError, ValueError):
            caps = []
        out.append({"id": f"ollama:{name}", "model": name, "provider": "ollama (local)",
                    "base_url": f"{ollama}/v1", "tools": "tools" in caps,
                    "selectable": "tools" in caps,
                    "size": (m.get("details") or {}).get("parameter_size"),
                    "thinking": "off" if name.startswith("qwen3") else None,
                    "label": f"{name} (local" + (", thinking off" if name.startswith("qwen3")
                                                 else "") + ")"
                    + ("" if "tools" in caps else " - no tools")})
    base, model = env.get("AGENT_BASE_URL", ""), env.get("AGENT_MODEL", "")
    if env.get("AGENT_API_KEY") and base and model and not _is_local_ollama(base):
        out.append({"id": "hosted", "model": model, "provider": "hosted (OpenAI-compatible)",
                    "base_url": base, "tools": None, "selectable": True, "key": "set",
                    "label": f"{model} (hosted)"})
    for m in out:
        m["current"] = m["model"] == current
    return out


def make_llm(entry: dict[str, Any], env: dict[str, str] | None = None):
    from console.llm_stream import StreamingLLM
    env = dict(os.environ) if env is None else env
    key = env.get("AGENT_API_KEY") if entry["id"] == "hosted" else None
    return StreamingLLM(entry["base_url"], entry["model"], key)
