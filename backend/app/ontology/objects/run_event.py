"""RunEvent — blueprint §8.7.

Structured, append-only event emitted by any runtime component. Aligns with the
existing L1-L6 Trace Bus; future: Trace Bus emits RunEvent rather than
TraceEvent, unifying persistent replay log with live SSE.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

EventType = Literal[
    # Runtime lifecycle
    "run_started", "run_ended", "step_started", "step_ended",
    "step_succeeded", "step_failed", "step_retried",
    # Policy / approval
    "policy_allow", "policy_deny", "approval_requested", "approval_granted", "approval_rejected",
    # L1 model
    "llm_request", "llm_response", "llm_error",
    # L4 stability
    "validation_ok", "validation_fail", "repair_attempt", "fallback_used",
    # L3 orchestration
    "state_transition", "plan_proposed", "plan_accepted",
    # L5 pipelines
    "phase_started", "phase_done",
    # L6 delivery
    "artifact_produced", "package_built",
    # L2 dialog
    "dialog_turn_start", "dialog_turn_end", "session_created",
    # Custom
    "custom",
]

Layer = Literal["L1", "L2", "L3", "L4", "L5", "L6", "sys"]


class RunEvent(BaseModel):
    """Structured runtime event (blueprint §8.7)."""

    event_id: str = Field(..., description="UUID hex.")
    event_type: EventType = Field(..., description="Category of the event.")
    timestamp: datetime
    payload: dict[str, object] = Field(default_factory=dict)
    # Cross-cutting correlation fields
    run_id: str | None = None
    step_run_id: str | None = None
    trace_id: str | None = None
    span_id: str | None = None
    # Agent Ops six-layer tag (matches existing Trace Bus convention)
    layer: Layer = "sys"
    component: str = ""
    kind: str = ""
    message: str = ""

    model_config = ConfigDict(extra="forbid")
