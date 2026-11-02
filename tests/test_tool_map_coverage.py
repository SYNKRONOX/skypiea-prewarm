"""Every tool class in our data vocabularies must map to a deployed tool server.

Reads configs/vocab/*.yaml and configs/tool_classes/*.yaml (read-only; owned by the data/model
sessions) and tools/tool_map.yaml (the single source of truth for class -> server).
Skips cleanly while those vocab files do not exist yet.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tools.common.toolmap import TOOL_MAP_PATH, load

ROOT = Path(__file__).resolve().parent.parent
VOCAB_GLOBS = ["configs/vocab/*.yaml", "configs/tool_classes/*.yaml"]


def _classes_in(doc) -> set[str]:
    """Class names from a vocab/tool_classes YAML: a `classes` list or mapping, else a list."""
    if isinstance(doc, list):
        return {str(c) for c in doc if isinstance(c, str)}
    if not isinstance(doc, dict):
        return set()
    out: set[str] = set()
    for key in ("classes", "vocab", "tool_classes"):
        v = doc.get(key)
        if isinstance(v, list):
            out |= {str(c) for c in v if isinstance(c, str)}
        elif isinstance(v, dict):
            out |= {str(c) for c in v}
    if isinstance(doc.get("default"), str):
        out.add(doc["default"])
    return out


def _vocab() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for pattern in VOCAB_GLOBS:
        for path in sorted(ROOT.glob(pattern)):
            classes = _classes_in(yaml.safe_load(path.read_text(encoding="utf-8")))
            if classes:
                found[str(path.relative_to(ROOT))] = classes
    return found


def test_tool_map_is_valid():
    tm = load(TOOL_MAP_PATH)
    assert 12 <= len(tm.servers) <= 16  # 16 agreed: edit_file has its own server
    assert tm.server_for("other") == tm.fallback
    assert tm.server_for("other_support") == tm.fallback
    for scenario_tool in ["order_lookup", "policy_check", "refund_issue", "promo_code_lookup",
                          "read_file", "run_tests", "format_diff", "post_comment", "edit_file"]:
        assert scenario_tool in tm.servers, scenario_tool


def test_every_vocab_class_has_a_server():
    vocab = _vocab()
    if not vocab:
        pytest.skip("no configs/vocab/*.yaml or configs/tool_classes/*.yaml yet")
    tm = load(TOOL_MAP_PATH)
    missing = {f"{src}: {c}" for src, classes in vocab.items() for c in classes
               if tm.server_for(c) is None}
    assert not missing, "classes without a server in tools/tool_map.yaml:\n" + "\n".join(
        sorted(missing))
