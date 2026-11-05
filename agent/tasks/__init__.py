"""Agent tasks: PRD Scenario A (support refund) and Scenario B (coding PR review).

Each task gives the system prompt (it describes the tools; the LLM picks its own order), one
user request per session (varied by a seed), the OpenAI tool schemas, the scenario graph used
to judge whether the LLM's order matched, and a scripted sequence for the ScriptedLLM.

Both scenarios run against predictor profile ``prd``, which is SYNTHETIC (trained on traces
sampled from the PRD scenario graphs): the coding profile's vocabulary has no format_diff.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.llm import FINISH

PROFILE = "prd"
PROFILE_NOTE = ("predictor profile prd is SYNTHETIC (PRD scenario graphs), not trained on real "
                "agent traces")


@dataclass(frozen=True)
class Task:
    scenario: str                       # "A" | "B"
    name: str
    system: str
    tools: list[dict[str, Any]]         # OpenAI function-tool schemas (incl. finish)
    start: str
    edges: dict[str, set[str]]          # tool -> allowed next tools ("<end>" = may stop)
    canonical: list[str]

    def user_prompt(self, i: int) -> str:
        raise NotImplementedError

    def scripted(self, i: int) -> list[tuple[str, dict[str, Any]]]:
        raise NotImplementedError

    @property
    def tool_names(self) -> list[str]:
        return [t["function"]["name"] for t in self.tools if t["function"]["name"] != FINISH]

    def params(self, tool: str) -> set[str]:
        for t in self.tools:
            if t["function"]["name"] == tool:
                return set(t["function"]["parameters"].get("properties", {}))
        return set()

    def follows_graph(self, seq: list[str]) -> bool:
        """True if seq is a walk of the scenario graph from its start that ends where the
        graph may end (every transition is an edge of the graph)."""
        if not seq or seq[0] != self.start:
            return False
        for a, b in zip(seq, seq[1:], strict=False):
            if b not in self.edges.get(a, set()):
                return False
        return "<end>" in self.edges.get(seq[-1], set())


def fn(name: str, desc: str, props: dict[str, Any], required: list[str] | None = None
       ) -> dict[str, Any]:
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props, "required": required or []}}}


FINISH_TOOL = fn(FINISH, "Call this exactly once when the task is complete, with a one-line "
                 "summary of what you did.", {"summary": {"type": "string"}}, ["summary"])


def get(scenario: str) -> Task:
    from agent.tasks.coding import TASK as B
    from agent.tasks.support_refund import TASK as A
    return {"A": A, "B": B}[scenario.upper()]
