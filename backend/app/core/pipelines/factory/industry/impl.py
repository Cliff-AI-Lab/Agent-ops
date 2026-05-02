"""IndustryRouterImpl - LLM-backed classifier for NL -> industry+scenario+multi-agent.

Strategy:
  - LLM produces strict JSON via system prompt (no structured-output API
    dependency; OpenAI-compatible gateway is the lowest common denominator).
  - JSON validated by IndustryClassification Pydantic; one retry on parse fail.
  - Heuristic fallback when LLM fails twice: keyword-based 通用 + multi_agent=False.

Per [[资产中心/横切-LLM模型路由表]]: classify is "结构化抽取" / 中-小 task.
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
from app.core.trace.bus import emit

logger = logging.getLogger(__name__)


_CODE_FENCE_RE = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.DOTALL)


_SYSTEM_PROMPT = """You are the Industry Router for an agent factory.

Your job: classify a natural-language requirement into:
  - industry_code: 2-digit code from this taxonomy:
      01 通用 (cross-industry, default)
      02 金融
      03 制造
      04 能源
      05 交通
      06 医疗
      07 教育
      08 政务
      09 零售
      10 媒体文娱
      11 通信
      12 智慧城市
  - primary: the matching name in Chinese
  - sub: industry-specific sub-segment if confident, else null
  - business_scenario: short Chinese business label (客服/风控/...) or null
  - is_multi_agent:
      true iff the request needs multiple specialized agents coordinating
      (e.g. customer service triage with FAQ + booking + complaints).
      false for single-workflow tasks (e.g. ETL daily report).
  - confidence: 0.0-1.0
  - reasoning: 1-2 sentences in Chinese explaining the classification

Output ONLY valid JSON, no commentary, no markdown fences.

Schema:
{
  "industry_code": "01",
  "primary": "通用",
  "sub": null,
  "business_scenario": "...",
  "is_multi_agent": false,
  "confidence": 0.85,
  "reasoning": "..."
}
"""


_REPAIR_PROMPT_TEMPLATE = """Your previous JSON output failed validation:

ERROR: {error}

PREVIOUS OUTPUT:
{previous}

Re-emit ONLY valid JSON matching the original schema. No commentary."""


def _strip_code_fence(text: str) -> str:
    """Remove ```json ... ``` wrapping if present."""
    m = _CODE_FENCE_RE.search(text)
    return m.group(1).strip() if m else text.strip()


def _heuristic_fallback(nl: str) -> IndustryClassification:
    """Keyword-only fallback when LLM fails twice. Defaults to 通用 single-agent."""
    nl_lower = nl.lower()
    multi_agent_keywords = ("客服", "三方", "多个智能体", "三个 agent", "triage", "supervisor")
    is_multi = any(k in nl_lower for k in multi_agent_keywords)
    return IndustryClassification(
        industry_code="01",
        primary="通用",
        sub=None,
        business_scenario=None,
        is_multi_agent=is_multi,
        confidence=0.30,
        reasoning="LLM 路由失败，启发式兜底（默认 01 通用）",
    )


class IndustryRouterImpl:
    """Default LLM-backed Industry Router."""

    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
        model_router: Optional[ModelRouter] = None,
    ) -> None:
        self._llm = llm_client
        self._router = model_router or ModelRouter()

    async def classify(self, nl: str) -> IndustryClassification:
        if not nl or not nl.strip():
            raise ValueError("nl cannot be empty")

        emit("L3", "IndustryRouter", "classify_start", f"nl_len={len(nl)}")

        if self._llm is None:
            self._llm = LLMClient()

        model = self._router.resolve(task_type="结构化抽取", size="中")

        messages = [
            ChatMessage(role="system", content=_SYSTEM_PROMPT),
            ChatMessage(role="user", content=nl),
        ]

        try:
            classification = await self._call_and_parse(model, messages)
        except (ValidationError, json.JSONDecodeError, ValueError) as exc1:
            logger.warning("IndustryRouter first attempt failed: %s", exc1)
            try:
                repair_msg = _REPAIR_PROMPT_TEMPLATE.format(
                    error=str(exc1), previous="(see prior turn)"
                )
                messages.append(ChatMessage(role="user", content=repair_msg))
                classification = await self._call_and_parse(model, messages)
            except (ValidationError, json.JSONDecodeError, ValueError) as exc2:
                logger.warning("IndustryRouter retry failed: %s", exc2)
                classification = _heuristic_fallback(nl)
                emit(
                    "L3",
                    "IndustryRouter",
                    "classify_fallback",
                    "heuristic fallback after 2 LLM failures",
                    data={"error": str(exc2)},
                )
                return classification

        emit(
            "L3",
            "IndustryRouter",
            "classify_done",
            f"industry={classification.industry_code}/{classification.primary} "
            f"scenario={classification.business_scenario} "
            f"multi_agent={classification.is_multi_agent} "
            f"conf={classification.confidence:.2f}",
            data=classification.model_dump(),
        )
        return classification

    async def _call_and_parse(
        self, model: str, messages: list[ChatMessage]
    ) -> IndustryClassification:
        resp = await self._llm.chat(model=model, messages=messages, temperature=0.0)
        text = _strip_code_fence(resp.content or "")
        data = json.loads(text)
        return IndustryClassification.model_validate(data)
