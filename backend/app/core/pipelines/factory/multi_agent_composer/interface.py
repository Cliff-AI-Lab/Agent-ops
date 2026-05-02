"""Phase 7 MultiAgentComposer - emits deployable runtime code from MultiAgentSpec.

Per [[Phase-7-多智能体与行业理解]] § 双阶段调度 final stage:
  IndustryDesigner -> MultiAgentSpec -> MultiAgentComposer (this) -> deployable code

Runtime locked: OpenAI Agents SDK Python.
Pure template + AST validation; no LLM at compile time.
"""
from __future__ import annotations

from typing import Protocol

from app.core.pipelines.factory.ir.multi_agent import MultiAgentSpec


class MultiAgentComposer(Protocol):
    """Compile MultiAgentSpec to runtime code string (single Python file)."""

    def compile(self, spec: MultiAgentSpec) -> str:
        """Return self-contained Python source for the multi-agent system.

        Output convention:
          - Module-level: imports, guardrail decorators, agent declarations,
            handoff wiring (second pass), public ENTRY_AGENT / ALL_AGENTS / SHARED_CONTEXT_FIELDS
          - Must be syntactically valid Python (caller may ast.parse()).
          - Idempotent: same spec -> same output (no timestamps, no random).
        """
        ...
