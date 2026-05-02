"""Phase 7 IndustryDesigner - stage 2 of two-stage scheduler.

Stage 1: IndustryRouter classifies NL -> IndustryClassification
Stage 2: this layer's IndustryDesigner takes (NL, Classification) ->
         MultiAgentSpec (when classification.is_multi_agent)

Per [[Phase-7-多智能体与行业理解]] § 双阶段调度.

Each industry has its own concrete Designer:
  GeneralDesigner       industry_code='01' (通用 - default fallback for any industry)
  FinanceDesigner       '02' (金融, V2.1.1+)
  ManufacturingDesigner '03' (制造, V2.1.1+)
  ... etc

Concrete designers are selected at runtime via DesignerRegistry (TBD V2.1.1).
For V2.1.0 W1 we ship the GeneralDesigner only.
"""
from __future__ import annotations

from typing import Protocol

from app.core.pipelines.factory.industry.interface import IndustryClassification
from app.core.pipelines.factory.ir.multi_agent import MultiAgentSpec


class IndustryDesigner(Protocol):
    """Stage 2 of Phase 7 scheduler. One impl per industry (or one general fallback)."""

    industry_code: str  # '01'..'12' per 横切-行业分类.md

    async def design_multi_agent(
        self,
        nl: str,
        classification: IndustryClassification,
    ) -> MultiAgentSpec:
        """Produce a MultiAgentSpec from NL + already-classified context.

        Caller (Phase 7 orchestrator) only invokes this when
        classification.is_multi_agent is True. Single-workflow path bypasses
        Designer entirely and goes straight to FactoryPipeline.

        Implementations should:
          - Extract triage + specialists + handoffs via LLM
          - Add standard guardrails (at minimum: relevance + jailbreak)
          - Set runtime='openai_agents_sdk' (locked per 决策记录)
          - Validate the resulting spec via spec.validate_graph()
          - Raise ValueError if structural issues detected
        """
        ...
