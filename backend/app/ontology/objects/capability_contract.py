"""CapabilityContract — blueprint §8.1.

A CapabilityContract is a versioned, typed declaration of an agent's ability.
Every capability must exist as a contract before prompt tuning (charter §3.2).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

RiskLevel = Literal["low", "medium", "high", "critical"]
ActivationStatus = Literal["draft", "active", "deprecated", "archived"]


class CapabilityContract(BaseModel):
    """Typed, versioned contract for a single agent capability (blueprint §8.1)."""

    capability_id: str = Field(..., description="Stable identifier, e.g. 'ui.plan_blueprint'.")
    version: str = Field(..., description="Semver (e.g. '0.1.0').")
    name: str = Field(..., description="Human-readable display name.")
    description: str = Field(..., description="One-paragraph intent + expected use.")
    owner: str = Field(..., description="Team or individual responsible.")
    input_schema: dict[str, object] = Field(
        ..., description="JSON Schema of the expected input payload."
    )
    output_schema: dict[str, object] = Field(
        ..., description="JSON Schema the output MUST match (enforced by L4 Verifier)."
    )
    allowed_tools: list[str] = Field(
        default_factory=list,
        description="Explicit whitelist of tool ids this capability may invoke.",
    )
    forbidden_actions: list[str] = Field(
        default_factory=list,
        description="Action categories this capability must never perform.",
    )
    risk_level: RiskLevel = Field(default="low")
    cost_budget: float | None = Field(
        default=None, description="Max USD-equivalent per single run; null = inherit workflow default."
    )
    latency_budget_ms: int | None = Field(
        default=None, description="Soft wall-clock budget; null = inherit."
    )
    quality_checks: list[str] = Field(
        default_factory=list,
        description="Names of quality rules (e.g. 'schema-valid', 'evidence-required').",
    )
    fallback_capabilities: list[str] = Field(
        default_factory=list,
        description="Ordered list of capability_ids used when repair / recovery is triggered.",
    )
    eval_suite_ids: list[str] = Field(
        default_factory=list,
        description="Eval suites that gate changes to this capability.",
    )
    activation_status: ActivationStatus = Field(default="draft")
    intent_keywords: list[str] = Field(
        default_factory=list,
        description="Natural-language intent phrases used by IntentRouter to retrieve this capability (e.g. '生成 UI 蓝图', 'plan pages from spec').",
    )
    composes: list[str] = Field(
        default_factory=list,
        description="Lower-tier ids this capability composes (typically tool_ids). Empty for atomic compositions; populated for true 'composite' tier members.",
    )

    model_config = ConfigDict(extra="forbid")
