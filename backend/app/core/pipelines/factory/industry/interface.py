"""Phase 7 IndustryRouter - the first stage of the two-stage scheduler.

Per [[Phase-7-多智能体与行业理解]] § 双阶段调度:
  Stage 1: IndustryRouter classifies NL -> industry + scenario + is_multi_agent
  Stage 2: industry-specific Designer produces single workflow OR MultiAgentSpec

This module defines the Protocol; concrete implementation in impl.py uses LLM.
"""
from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field, field_validator


class IndustryClassification(BaseModel):
    """Output of IndustryRouter.classify().

    Maps to [[资产中心/横切-行业分类]] (12 一级行业).
    is_multi_agent decides whether downstream goes single-workflow or
    multi-agent path.
    """

    industry_code: str = Field(
        min_length=2,
        max_length=2,
        description='Two-digit industry code: 01..12 per 横切-行业分类.md',
    )
    primary: str = Field(
        min_length=1,
        description="一级行业 name, e.g. 通用 / 金融 / 制造 / 教育 / 医疗 / ...",
    )
    sub: str | None = Field(
        default=None,
        description="子行业 if classifier confident, e.g. 银行 / 重工",
    )
    business_scenario: str | None = Field(
        default=None,
        description="business scenario, e.g. 客服 / 风控告警 / 售前提案",
    )
    is_multi_agent: bool = Field(
        description=(
            "True iff the request inherently needs multiple coordinating "
            "specialists (e.g. customer service triage). False for "
            "single-workflow tasks (e.g. ETL report)."
        )
    )
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = Field(
        min_length=10,
        description="LLM rationale; recorded to trace for debuggability",
    )

    @field_validator("industry_code")
    @classmethod
    def _code_in_range(cls, v: str) -> str:
        if not v.isdigit() or not (1 <= int(v) <= 12):
            raise ValueError(f"industry_code must be 01..12, got {v!r}")
        return v


class IndustryRouter(Protocol):
    """Stage 1 of Phase 7 two-stage scheduler.

    Input: natural language requirement (single user message).
    Output: structured industry + scenario + multi-agent verdict.
    """

    async def classify(self, nl: str) -> IndustryClassification:
        ...
