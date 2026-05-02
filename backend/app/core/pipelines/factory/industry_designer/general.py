"""GeneralDesigner - the cross-industry default Designer (industry_code='01').

V2.1.0 W1 seed: handles 客服 / 多 agent 协调 use cases regardless of industry
since 12 行业 have not yet shipped their own Designers.

Strategy:
  - LLM extracts triage + specialists from NL
  - Standard guardrails added unconditionally (relevance + jailbreak)
  - Standard shared context fields based on business_scenario
  - Default agent_class=customer_service when scenario contains 客服/客户

Per LLM model routing: this is "结构化抽取" / 中 task.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

from pydantic import ValidationError

from app.core.llm.client import ChatMessage, LLMClient
from app.core.llm.model_router import ModelRouter
from app.core.pipelines.factory.industry.interface import IndustryClassification
from app.core.pipelines.factory.ir.multi_agent import (
    GuardrailSpec,
    HandoffEdge,
    IndustryTag,
    MultiAgentSpec,
    SharedContextField,
    SpecialistSpec,
    TriageSpec,
)
from app.core.trace.bus import emit

logger = logging.getLogger(__name__)


_CODE_FENCE_RE = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.DOTALL)


_EXTRACTION_PROMPT = """You are a multi-agent system architect.

Given a Chinese natural-language requirement, extract the multi-agent
structure. Output JSON ONLY (no commentary, no markdown fence).

Schema:
{
  "system_name": "...",
  "specialists": [
    {
      "id": "alnum_id",
      "name": "Chinese name",
      "description": "10+ char role description in Chinese",
      "nl_brief": "10+ char single-line NL brief for this specialist",
      "handoff_targets": ["other_specialist_id", ...]
    }
  ],
  "triage_initial_targets": ["specialist_id_1", ...],
  "shared_context_fields": [
    {"name": "field_name", "type": "string|integer|boolean|object", "description": "5+ char"}
  ]
}

Rules:
- 2-7 specialists. Group related responsibilities into single specialist.
- Always include an "faq" specialist for general policy/info questions.
- All specialist ids must be alnum (letters/digits/underscore/hyphen) only.
- handoff_targets must reference other specialist ids (or empty list).
- Triage initial_targets should include all specialists (Triage routes everything).
- shared_context_fields: 0-5 fields representing cross-agent state
  (e.g., user_id, confirmation_number, current_session_id).
"""


_REPAIR_TEMPLATE = """Your previous JSON output failed validation:

ERROR: {error}

Re-emit ONLY valid JSON matching the schema. No commentary."""


def _strip_code_fence(text: str) -> str:
    m = _CODE_FENCE_RE.search(text)
    return m.group(1).strip() if m else text.strip()


def _default_guardrails() -> list[GuardrailSpec]:
    """Phase 7 minimum guardrails - applied to every multi-agent system."""
    return [
        GuardrailSpec(
            kind="relevance",
            description="Block off-topic input that diverges from the system's stated purpose; redirect politely.",
            blocking=True,
        ),
        GuardrailSpec(
            kind="jailbreak",
            description="Block prompt injection / system instruction extraction attempts.",
            blocking=True,
        ),
    ]


def _agent_class_for_scenario(business_scenario: Optional[str]) -> str:
    """Map business_scenario keyword to one of 7 L4 agent_class types."""
    if not business_scenario:
        return "customer_service"
    s = business_scenario
    if any(k in s for k in ("文档", "归档")):
        return "doc"
    if any(k in s for k in ("语音", "电话")):
        return "voice"
    if any(k in s for k in ("数据", "分析", "报表", "BI")):
        return "data_analytics"
    if any(k in s for k in ("研究", "调研", "对比")):
        return "research"
    if any(k in s for k in ("方案", "提案", "售前")):
        return "proposal"
    if any(k in s for k in ("建模", "本体", "知识图谱")):
        return "modeling"
    return "customer_service"


class GeneralDesigner:
    """V2.1.0 W1 seed Designer - 通用 (industry_code='01') fallback for any industry."""

    industry_code = "01"

    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
        model_router: Optional[ModelRouter] = None,
    ) -> None:
        self._llm = llm_client
        self._router = model_router or ModelRouter()

    async def design_multi_agent(
        self,
        nl: str,
        classification: IndustryClassification,
    ) -> MultiAgentSpec:
        if not classification.is_multi_agent:
            raise ValueError(
                "GeneralDesigner.design_multi_agent called with is_multi_agent=False; "
                "use FactoryPipeline for single-agent workflows."
            )
        if not nl or not nl.strip():
            raise ValueError("nl cannot be empty")

        emit(
            "L3",
            "GeneralDesigner",
            "design_start",
            f"industry={classification.industry_code} scenario={classification.business_scenario}",
        )

        if self._llm is None:
            self._llm = LLMClient()

        extracted = await self._extract(nl)
        spec = self._assemble(nl, classification, extracted)

        issues = spec.validate_graph()
        if issues:
            raise ValueError(f"GeneralDesigner produced spec with structural issues: {issues}")

        emit(
            "L3",
            "GeneralDesigner",
            "design_done",
            f"specialists={len(spec.specialists)} handoffs={len(spec.handoffs)}",
            data={"system_name": spec.name, "specialist_ids": [s.id for s in spec.specialists]},
        )
        return spec

    async def _extract(self, nl: str) -> dict:
        model = self._router.resolve(task_type="结构化抽取", size="中")
        messages = [
            ChatMessage(role="system", content=_EXTRACTION_PROMPT),
            ChatMessage(role="user", content=nl),
        ]
        try:
            return await self._call_and_parse(model, messages)
        except (json.JSONDecodeError, ValueError) as exc1:
            logger.warning("GeneralDesigner first attempt failed: %s", exc1)
            messages.append(
                ChatMessage(role="user", content=_REPAIR_TEMPLATE.format(error=str(exc1)))
            )
            return await self._call_and_parse(model, messages)

    async def _call_and_parse(self, model: str, messages: list[ChatMessage]) -> dict:
        resp = await self._llm.chat(model=model, messages=messages, temperature=0.0)
        text = _strip_code_fence(resp.content or "")
        data = json.loads(text)
        if not isinstance(data, dict) or "specialists" not in data:
            raise ValueError("response missing required 'specialists' key")
        if not isinstance(data["specialists"], list) or len(data["specialists"]) < 2:
            raise ValueError("specialists must be a list of >= 2 entries")
        return data

    def _assemble(
        self,
        nl: str,
        classification: IndustryClassification,
        extracted: dict,
    ) -> MultiAgentSpec:
        agent_class = _agent_class_for_scenario(classification.business_scenario)
        specialists: list[SpecialistSpec] = []
        for raw in extracted["specialists"]:
            try:
                specialists.append(
                    SpecialistSpec(
                        id=str(raw["id"]),
                        name=str(raw["name"]),
                        agent_class=agent_class,
                        description=str(raw["description"]),
                        nl_brief=str(raw["nl_brief"]),
                        handoff_targets=list(raw.get("handoff_targets", [])),
                    )
                )
            except (KeyError, ValidationError) as e:
                logger.warning("skipping malformed specialist %s: %s", raw, e)
                continue

        if len(specialists) < 1:
            raise ValueError("no valid specialists extracted from LLM")

        # Filter each specialist's handoff_targets to only valid specialist ids,
        # so validate_graph passes even when LLM hallucinates unknown ids.
        valid_ids = {s.id for s in specialists}
        for s in specialists:
            s.handoff_targets = [t for t in s.handoff_targets if t in valid_ids]

        triage_targets = list(extracted.get("triage_initial_targets") or [])
        # If LLM didn't fill triage_initial_targets, default to all specialists.
        if not triage_targets:
            triage_targets = [s.id for s in specialists]
        else:
            triage_targets = [t for t in triage_targets if t in valid_ids]

        shared_ctx: list[SharedContextField] = []
        for raw in extracted.get("shared_context_fields") or []:
            try:
                shared_ctx.append(
                    SharedContextField(
                        name=str(raw["name"]),
                        type=raw["type"],
                        description=str(raw["description"]),
                    )
                )
            except (KeyError, ValidationError) as e:
                logger.debug("skipping malformed shared_context_field %s: %s", raw, e)
                continue

        # Build handoffs from each specialist's handoff_targets list.
        handoffs: list[HandoffEdge] = []
        valid_ids = {s.id for s in specialists}
        for s in specialists:
            for tgt in s.handoff_targets:
                if tgt in valid_ids:
                    handoffs.append(
                        HandoffEdge(
                            from_specialist=s.id,
                            to_specialist=tgt,
                            when=f"User intent shifts toward {tgt}'s domain",
                        )
                    )

        system_name = extracted.get("system_name") or f"Multi-Agent System ({classification.business_scenario or 'general'})"

        return MultiAgentSpec(
            name=str(system_name),
            description=(
                f"Multi-agent system designed by GeneralDesigner from NL. "
                f"Industry: {classification.primary} ({classification.industry_code}). "
                f"Scenario: {classification.business_scenario or 'unspecified'}. "
                f"Specialists: {len(specialists)}."
            ),
            industry=IndustryTag(
                code=classification.industry_code,
                primary=classification.primary,
                sub=classification.sub,
                business_scenario=classification.business_scenario,
            ),
            triage=TriageSpec(
                name="Triage Agent",
                system_prompt_id="prompt.triage.general.v1",
                initial_handoff_targets=triage_targets,
            ),
            specialists=specialists,
            handoffs=handoffs,
            guardrails=_default_guardrails(),
            shared_context=shared_ctx,
        )
