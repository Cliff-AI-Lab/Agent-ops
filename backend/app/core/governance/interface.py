"""Governance layer interfaces (Phase 6 V2.2).

Per [[Phase-6-治理与可观测]]: cost gate / dependency graph / 灰度回灌.

This module defines the Protocols. Concrete impls in cost_estimator.py
and cost_budget.py.
"""
from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field


class CostEstimate(BaseModel):
    """Estimated cost of one build (single or multi-agent path).

    All amounts in CNY (Chinese Yuan). Storage rounds to 4 decimals.
    """

    estimated_total_cny: float = Field(ge=0.0)
    breakdown: dict[str, float] = Field(
        default_factory=dict,
        description="Cost components: classify / intent_parse / resolver / designer / per atom / etc.",
    )
    estimated_llm_calls: int = Field(ge=0, default=0)
    estimated_tokens_in: int = Field(ge=0, default=0)
    estimated_tokens_out: int = Field(ge=0, default=0)
    notes: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0, default=0.7)


class CostBudgetDecision(BaseModel):
    """Result of CostBudget.check()."""

    approved: bool
    estimate: CostEstimate
    threshold_cny: float
    reason: str = ""


class CostEstimator(Protocol):
    """Estimates pre-build cost of a factory invocation."""

    def estimate_single(self, nl: str) -> CostEstimate:
        """Single-agent path estimate (FactoryPipeline)."""
        ...

    def estimate_multi(self, nl: str, anticipated_specialists: int = 5) -> CostEstimate:
        """Multi-agent path estimate (MultiAgentFactoryPipeline)."""
        ...


class CostBudget(Protocol):
    """Gates a build by checking estimate against threshold."""

    threshold_cny: float

    def check(self, estimate: CostEstimate) -> CostBudgetDecision:
        ...
