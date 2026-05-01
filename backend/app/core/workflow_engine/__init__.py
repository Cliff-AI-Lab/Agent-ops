"""Workflow Engine — deterministic executor for WorkflowSpec (blueprint M3).

The engine takes an authoritative WorkflowSpec (typically produced by the
IntentRouter / Planner) and runs it step by step against the live registry,
yielding typed events. It does NOT do any planning itself — the spec is the
audit boundary (blueprint §3.1 deterministic shell).
"""
from app.core.workflow_engine.executor import (
    StepResult,
    WorkflowEngine,
    WorkflowEvent,
    WorkflowRunResult,
)
from app.core.workflow_engine.invoker import CapabilityInvoker, InvocationResult

__all__ = [
    "WorkflowEngine",
    "WorkflowEvent",
    "WorkflowRunResult",
    "StepResult",
    "CapabilityInvoker",
    "InvocationResult",
]
