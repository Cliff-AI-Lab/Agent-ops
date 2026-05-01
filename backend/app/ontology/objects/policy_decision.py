"""PolicyDecision — blueprint §7 + §14.2.

Emitted by the Policy Engine before any external write / irreversible action.
Prepared here in Batch B for Batch G (Policy boundary).
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Decision = Literal["allow", "deny", "require_approval"]
ActionCategory = Literal["read", "generate", "propose", "export", "write", "irreversible"]


class PolicyDecision(BaseModel):
    """Outcome of evaluating a policy against a pending Action."""

    decision_id: str
    decision: Decision
    action_category: ActionCategory
    action_name: str = Field(..., description="capability_id or specific operation key.")
    reason_code: str = Field(
        ..., description="Stable identifier of the rule that produced this decision."
    )
    reason_message: str = ""
    policy_rule_id: str | None = None
    run_id: str | None = None
    step_run_id: str | None = None
    evaluated_at: datetime | None = None
    context: dict[str, object] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")
