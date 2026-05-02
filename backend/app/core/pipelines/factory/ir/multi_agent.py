"""Phase 7 IR: MultiAgentSpec — composed multi-agent application.

A MultiAgentSpec describes a deployable system of:
  - 1 Triage / supervisor agent (entry point, classifier + router)
  - N specialist agents (each is an L4 specimen produced by FactoryPipeline)
  - Handoff edges between specialists
  - Guardrails (input filters: relevance / jailbreak / etc.)
  - Shared context fields (cross-agent state)

Inspired by openai/openai-cs-agents-demo (cs-agents):
  TriageAgent + 5 specialists + Handoffs + Guardrails

Per [[Phase-7-多智能体与行业理解]] § IR 设计 + locked decisions:
  Runtime target: OpenAI Agents SDK
  L4.5 layer in the 6-layer asset model
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


class IndustryTag(BaseModel):
    """Industry classification tag (per [[资产中心/横切-行业分类]])."""

    code: str = Field(min_length=2, max_length=2, description='Two-digit industry code, "01"..."12"')
    primary: str = Field(min_length=1, description="一级行业, e.g. 通用 / 金融 / 制造")
    sub: Optional[str] = Field(default=None, description="子行业, e.g. 银行 / 重工")
    business_scenario: Optional[str] = Field(
        default=None, description="业务场景, e.g. 客服 / 风控告警 / 售前提案"
    )


class HandoffEdge(BaseModel):
    """A directed handoff: specialist A can transfer the user to specialist B.

    Trigger conditions are LLM-judged (matched in prompt) at runtime.
    """

    from_specialist: str = Field(description="source specialist id")
    to_specialist: str = Field(description="target specialist id")
    when: str = Field(
        min_length=5,
        description="natural language description of when this handoff fires",
    )


class GuardrailSpec(BaseModel):
    """Cross-cutting input/output filter applied at every turn.

    Common kinds:
      relevance   block off-topic
      jailbreak   block prompt injection / system instruction extraction
      pii         redact / refuse personal info
      compliance  industry-specific policy gates
    """

    kind: Literal["relevance", "jailbreak", "pii", "compliance"]
    description: str = Field(min_length=10)
    blocking: bool = Field(default=True, description="if False, only logs warning; doesn't stop the turn")


class SharedContextField(BaseModel):
    """A piece of state shared across all specialists in the system.

    e.g. confirmation_number, user_tier, current_flight_number.
    """

    name: str = Field(min_length=1)
    type: Literal["string", "integer", "float", "boolean", "object"]
    description: str = Field(min_length=5)
    initial_value: object | None = None


class TriageSpec(BaseModel):
    """The supervisor / entry agent.

    Receives every user turn first, decides which specialist (or itself)
    should handle this turn. Emits a handoff to that specialist.
    """

    name: str = Field(default="Triage Agent")
    system_prompt_id: str = Field(
        description="prompt asset id, e.g. prompt.triage.airline_cs.v1"
    )
    initial_handoff_targets: list[str] = Field(
        default_factory=list,
        description="specialist ids the triage can hand off to from cold start",
    )
    fallback_message: str = Field(
        default="抱歉，我不太确定，让我帮您联系合适的同事。",
        min_length=5,
    )


class SpecialistSpec(BaseModel):
    """One specialist agent. Each becomes an L4 specimen via FactoryPipeline.

    The `agent_class` corresponds to one of the 7 L4 agent types:
      文档 / 客服 / 语音 / 数据分析 / 研究 / 方案-提案 / 建模-设计

    `referenced_atoms` and `referenced_prompts` are asset_ids resolved by
    the Resolver when this specialist is built. The MultiAgentComposer
    invokes FactoryPipeline once per specialist, threading the agent_class
    and industry into the build context.
    """

    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1)
    agent_class: Literal[
        "doc", "customer_service", "voice", "data_analytics",
        "research", "proposal", "modeling",
    ]
    description: str = Field(min_length=10, description="role description for triage routing")
    nl_brief: str = Field(
        min_length=10,
        description="single-line NL brief; fed to FactoryPipeline.build() to produce the specialist's workflow",
    )
    handoff_targets: list[str] = Field(
        default_factory=list,
        description="other specialist ids this one can hand off to",
    )
    tools: list[str] = Field(
        default_factory=list,
        description="atom ids exposed as tools (e.g. atom.db.postgres.v1)",
    )

    @field_validator("id")
    @classmethod
    def _id_slug(cls, v: str) -> str:
        if not v.replace("_", "").replace("-", "").isalnum():
            raise ValueError("id must be alnum + _ -")
        return v


class MultiAgentSpec(BaseModel):
    """Phase 7 IR: complete multi-agent application spec.

    Output of: Industry Designer (per [[Phase-7-多智能体与行业理解]] § 双阶段调度)
    Input to:  MultiAgentComposer (calls FactoryPipeline.build N times +
               emits OpenAI Agents SDK runtime code)
    """

    schema_version: Literal["1.0"] = "1.0"
    name: str = Field(min_length=1, description="system name, e.g. 'Airline Customer Service System'")
    description: str = Field(min_length=20)
    industry: IndustryTag

    triage: TriageSpec
    specialists: list[SpecialistSpec] = Field(min_length=1, max_length=20)
    handoffs: list[HandoffEdge] = Field(default_factory=list)
    guardrails: list[GuardrailSpec] = Field(default_factory=list)
    shared_context: list[SharedContextField] = Field(default_factory=list)

    runtime: Literal["openai_agents_sdk"] = "openai_agents_sdk"

    @field_validator("specialists")
    @classmethod
    def _unique_specialist_ids(cls, v: list[SpecialistSpec]) -> list[SpecialistSpec]:
        ids = [s.id for s in v]
        if len(ids) != len(set(ids)):
            raise ValueError("specialist ids must be unique")
        return v

    def specialist_ids(self) -> set[str]:
        return {s.id for s in self.specialists}

    def validate_graph(self) -> list[str]:
        """Return list of structural issues, [] if clean."""
        issues: list[str] = []
        sids = self.specialist_ids()
        for tgt in self.triage.initial_handoff_targets:
            if tgt not in sids:
                issues.append(f"triage points to unknown specialist {tgt!r}")
        for h in self.handoffs:
            if h.from_specialist not in sids:
                issues.append(f"handoff from unknown specialist {h.from_specialist!r}")
            if h.to_specialist not in sids:
                issues.append(f"handoff to unknown specialist {h.to_specialist!r}")
        for s in self.specialists:
            for tgt in s.handoff_targets:
                if tgt not in sids:
                    issues.append(f"specialist {s.id!r} hands off to unknown {tgt!r}")
        return issues
