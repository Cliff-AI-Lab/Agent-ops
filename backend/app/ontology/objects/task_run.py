"""TaskRun / StepRun — blueprint §8.4, §8.5.

Every execution of a WorkflowSpec produces one TaskRun; each step produces a
StepRun. These are the ground truth for replay, observability, and eval.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

RunStatus = Literal[
    "pending", "running", "succeeded", "failed", "blocked", "waiting_approval", "cancelled"
]


class CostSummary(BaseModel):
    """Aggregated cost across a run."""

    input_tokens: int = 0
    output_tokens: int = 0
    usd_estimate: float = 0.0
    elapsed_ms: int = 0

    model_config = ConfigDict(extra="forbid")


class TaskRun(BaseModel):
    """One execution of a WorkflowSpec (blueprint §8.4)."""

    run_id: str = Field(..., description="Globally unique (UUID hex).")
    workflow_id: str
    workflow_version: str
    input_ref: str = Field(..., description="Artifact ref or object-store URI of the input payload.")
    status: RunStatus = "pending"
    plan_ref: str | None = Field(default=None, description="Advisory Planner output artifact ref.")
    started_at: datetime | None = None
    ended_at: datetime | None = None
    trace_id: str | None = None
    cost_summary: CostSummary = Field(default_factory=CostSummary)
    final_report_ref: str | None = None
    error_ref: str | None = None

    model_config = ConfigDict(extra="forbid")


class StepRun(BaseModel):
    """One execution of a WorkflowStep within a TaskRun (blueprint §8.5)."""

    step_run_id: str
    run_id: str
    step_id: str
    capability_id: str
    agent_id: str | None = None
    status: RunStatus = "pending"
    attempt: int = 1
    input_ref: str | None = None
    output_ref: str | None = None
    verification_ref: str | None = Field(
        default=None, description="VerificationReport artifact ref produced by L4 Verifier."
    )
    policy_ref: str | None = Field(
        default=None, description="PolicyDecision artifact ref from L4 Policy engine."
    )
    error_ref: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None

    model_config = ConfigDict(extra="forbid")
