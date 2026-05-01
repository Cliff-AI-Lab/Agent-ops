"""WorkflowSpec — blueprint §8.3.

Declarative, versioned description of a multi-step task. Executed by the
deterministic Workflow Engine (blueprint M3). Planner output is advisory only;
the WorkflowSpec is authoritative.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

TriggerType = Literal["manual", "scheduled", "event", "api"]
FailureAction = Literal["fail", "retry", "fallback", "block_for_approval", "skip"]


class WorkflowStep(BaseModel):
    """Single step in a WorkflowSpec."""

    id: str = Field(..., description="Step id, unique within a workflow.")
    capability: str = Field(..., description="capability_id to invoke for this step.")
    depends_on: list[str] = Field(
        default_factory=list,
        description="Step ids that must complete before this step may start.",
    )
    input_mapping: dict[str, str] = Field(
        default_factory=dict,
        description="Mapping from capability input field → source ref (e.g. 'inputs.rfp_path').",
    )
    on_failure: FailureAction = Field(default="fail")
    fallback_step: str | None = Field(
        default=None, description="Step id to jump to when on_failure=fallback."
    )
    max_retries: int = Field(default=0)
    timeout_ms: int | None = Field(default=None)
    approval_required: bool = Field(default=False)

    model_config = ConfigDict(extra="forbid")


class ApprovalNode(BaseModel):
    """Human approval gate in a workflow."""

    after_step: str
    required_role: str
    approval_prompt: str = ""
    timeout_seconds: int | None = None

    model_config = ConfigDict(extra="forbid")


class FailurePolicy(BaseModel):
    """Workflow-wide failure handling rule."""

    on_error_type: str = Field(..., description="Error class name (e.g. 'ToolTimeout').")
    action: FailureAction
    fallback_capability: str | None = None
    max_attempts: int = 1

    model_config = ConfigDict(extra="forbid")


class SuccessCriterion(BaseModel):
    """Terminal check: workflow is only successful if all criteria hold."""

    kind: Literal["artifact_exists", "verifier_score_gte", "field_matches"]
    value: str | float

    model_config = ConfigDict(extra="forbid")


class WorkflowSpec(BaseModel):
    """A declarative workflow (blueprint §8.3)."""

    workflow_id: str
    version: str
    name: str = ""
    description: str = ""
    trigger_type: TriggerType = "manual"
    inputs_schema: dict[str, object] = Field(default_factory=dict)
    steps: list[WorkflowStep] = Field(..., min_length=1)
    approval_nodes: list[ApprovalNode] = Field(default_factory=list)
    failure_policies: list[FailurePolicy] = Field(default_factory=list)
    success_criteria: list[SuccessCriterion] = Field(default_factory=list)
    export_artifacts: list[str] = Field(
        default_factory=list,
        description="Artifact types this workflow is expected to produce.",
    )

    model_config = ConfigDict(extra="forbid")
