"""AgentContract — third tier of the Agent Ops three-tier ontology.

Tier diagram::

    AgentContract       (this file)  ← 智能体层 / agent
        ↑ uses
    CapabilityContract  (capability_contract.py)  ← 组合层 / composite
        ↑ uses
    ToolContract        (tool_contract.py)  ← 原子层 / atom

An AgentContract is a complete, mission-scoped agent declaration that the
IntentRouter can retrieve and the Workflow Engine can execute. It is the
top of the registry pyramid and the unit that the Asset Hub publishes.

Difference from AgentCard:
- ``AgentCard`` (blueprint §8.2) carries transport-only metadata (endpoint,
  auth, latency) suitable for A2A discovery / interop.
- ``AgentContract`` is the full self-description: composition graph,
  intent keywords, runtime config, evaluation suite, asset provenance.
- An agent_id may be referenced by both — they are joinable on agent_id.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.ontology.objects.agent_card import AuthMode, CostProfile, TransportType
from app.ontology.objects.capability_contract import ActivationStatus, RiskLevel

AgentTier = Literal["agent"]


class AgentExample(BaseModel):
    """A canonical interaction example showing the agent's intent & flow."""

    title: str
    user_intent: str
    expected_outcome: str = Field(default="")

    model_config = ConfigDict(extra="forbid")


class AgentGenerationMetadata(BaseModel):
    """Provenance tracking for agents produced by Agent Ops itself."""

    generation_id: str | None = Field(
        default=None,
        description="UUID of the generation run that produced this agent (None for platform-native).",
    )
    source_session_id: str | None = None
    source_sop: str | None = Field(
        default=None,
        description="The original one-sentence SOP the user typed.",
    )
    generated_at: datetime | None = None
    quality_score: float | None = Field(
        default=None,
        description="0.0–1.0 — derived from eval pass rate, user feedback, repair attempts. Used by IntentRouter for ranking.",
    )
    usage_count: int = Field(default=0, description="How many times this agent has been retrieved/composed.")
    promoted_by: str | None = Field(
        default=None,
        description="User id that promoted this draft agent into the active registry (asset-hub gating).",
    )

    model_config = ConfigDict(extra="forbid")


class AgentContract(BaseModel):
    """Top-tier agent declaration — what the IntentRouter retrieves and ships.

    Composition rules (enforced by RegistryHub):
    - Every entry in ``capabilities_used`` MUST resolve to an active CapabilityContract.
    - Every entry in ``tools_used`` MUST resolve to an active ToolContract.
    - ``entry_capability`` MUST be one of ``capabilities_used`` (the agent's main step).
    """

    agent_id: str = Field(..., description="Stable id, e.g. 'agents.customer_service' or 'gen-2026-04-25-abc123'.")
    version: str = Field(default="0.1.0")
    name: str
    description: str
    owner: str = Field(default="agent-ops-platform")

    # Three-tier composition (key field — drives IntentRouter)
    capabilities_used: list[str] = Field(
        default_factory=list,
        description="composite-tier capability_ids this agent orchestrates.",
    )
    tools_used: list[str] = Field(
        default_factory=list,
        description="atom-tier tool_ids this agent invokes directly (in addition to those reached via capabilities).",
    )
    entry_capability: str | None = Field(
        default=None,
        description="The capability_id that serves as the agent's primary entrypoint (must be in capabilities_used).",
    )

    # Discoverability — used by IntentRouter and the Asset-Hub Wiki
    intent_keywords: list[str] = Field(
        default_factory=list,
        description="Natural-language phrases used to match user intent (中/EN free-form).",
    )
    tags: list[str] = Field(default_factory=list)
    examples: list[AgentExample] = Field(default_factory=list)

    # External I/O contract (what the agent accepts from / returns to a caller)
    input_schema: dict[str, object] = Field(default_factory=dict)
    output_schema: dict[str, object] = Field(default_factory=dict)

    # Transport (links to AgentCard for A2A interop; redundant copy here so the
    # agent contract is self-contained when downloaded from the Asset Hub).
    transport_type: TransportType = Field(default="local")
    endpoint: str | None = None
    auth_mode: AuthMode = Field(default="none")
    supports_streaming: bool = Field(default=False)
    supports_human_handoff: bool = Field(default=False)
    cost_profile: CostProfile = Field(default="standard")

    # Operational
    risk_level: RiskLevel = Field(default="low")
    cost_budget: float | None = None
    latency_budget_ms: int | None = None
    runtime_config: dict[str, object] = Field(
        default_factory=dict,
        description="Free-form runtime knobs (model name, max_tokens overrides, system prompt fragments).",
    )

    # Evaluation
    eval_suite_ids: list[str] = Field(default_factory=list)

    # Asset Hub provenance — non-null when this agent was generated by Agent Ops
    generation: AgentGenerationMetadata = Field(default_factory=AgentGenerationMetadata)
    is_generated: bool = Field(
        default=False,
        description="True when this agent was produced by an Agent Ops run (flows into asset_hub).",
    )

    activation_status: ActivationStatus = Field(default="draft")

    model_config = ConfigDict(extra="forbid")
