"""StructuredIntent: IR1 produced by IntentParser.

Abstract intent (verbs + data flow + constraints), NO atom binding.
Binding happens in Resolver -> ResolvedDAG.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class TriggerSpec(BaseModel):
    """Trigger that initiates the workflow."""

    type: Literal["cron", "webhook", "manual", "event"]
    cron_expr: str | None = None
    webhook_path: str | None = None
    event_source: str | None = None
    natural_language: str | None = Field(
        default=None,
        description="Original user time expression, e.g. '每周一上午9点'",
    )


class StepSpec(BaseModel):
    """One abstract action in the intent. Not bound to a specific atom."""

    id: str = Field(description="Step ID like 's1'")
    verb: str = Field(description="Abstract action: '查询数据库' / '撰写中文周报'")
    inputs: dict[str, Any] = Field(
        default_factory=dict,
        description="Input refs like {'data': '$s1.output'}",
    )
    expected_output_kind: str = Field(
        description="Output kind: 'tabular_data' / 'natural_language' / 'json_object' / 'void'"
    )
    constraints: dict[str, Any] = Field(default_factory=dict)
    suggested_subcategory: str | None = Field(
        default=None,
        description="Hint for Resolver: 'DB' / 'LLM' / 'Notify'",
    )


class OutputSpec(BaseModel):
    """Final output of the workflow."""

    name: str
    type: Literal["string", "json", "file", "url", "void"]
    sink: str | None = Field(
        default=None,
        description="Output destination, e.g. 'dingtalk:webhook' / 'email:user'",
    )


class Constraints(BaseModel):
    """Workflow-level constraints."""

    latency_p95_ms: int | None = None
    cost_ceiling_cny: float | None = None
    language: str = "zh"
    privacy: Literal["public", "internal", "confidential"] = "internal"


class StructuredIntent(BaseModel):
    """Final output of IntentParser. Input to Resolver."""

    schema_version: Literal["1.0"] = "1.0"
    goal: str = Field(description="One-line goal")
    trigger: TriggerSpec
    steps: list[StepSpec]
    outputs: list[OutputSpec]
    constraints: Constraints = Field(default_factory=Constraints)
    industry: dict[str, str] | None = Field(
        default=None,
        description="{'code': '01', 'primary': '通用', 'sub': '商业运营'}",
    )
    raw_user_input: str = Field(description="Original NL, for replay/debug")
