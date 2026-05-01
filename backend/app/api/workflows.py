"""Workflow Engine API (Batch D).

POST /api/workflows/run     — execute a WorkflowSpec end to end (SSE)
POST /api/workflows/run-sop — convenience: SOP → IntentRouter → run, all SSE
"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sse_starlette.sse import EventSourceResponse

from app.config import get_settings
from app.core.llm.client import LLMClient
from app.core.orchestrator.intent_router import IntentRouter
from app.core.workflow_engine import (
    WorkflowEngine,
    WorkflowEvent,
    WorkflowRunResult,
)
from app.ontology import WorkflowSpec

router = APIRouter(prefix="/api/workflows", tags=["workflows"])


class RunRequest(BaseModel):
    """Body for POST /api/workflows/run."""

    spec: WorkflowSpec
    inputs: dict[str, object] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class RunSopRequest(BaseModel):
    """Body for POST /api/workflows/run-sop — IntentRouter then Engine."""

    sop: str = Field(..., min_length=1)
    inputs: dict[str, object] = Field(default_factory=dict)
    light_model: str | None = None
    heavy_model: str | None = None

    model_config = ConfigDict(extra="forbid")


@router.post("/run")
async def run(req: RunRequest) -> EventSourceResponse:
    """Execute a pre-built WorkflowSpec, streaming each step as an SSE event."""
    engine = WorkflowEngine()

    async def event_stream():
        async for ev in engine.run(req.spec, dict(req.inputs)):
            if isinstance(ev, WorkflowEvent):
                yield {"event": ev.kind, "data": ev.model_dump_json()}
            elif isinstance(ev, WorkflowRunResult):
                yield {"event": "result", "data": ev.model_dump_json()}

    return EventSourceResponse(event_stream())


@router.post("/run-sop")
async def run_sop(req: RunSopRequest) -> EventSourceResponse:
    """SOP → IntentRouter (plan) → Engine (execute), full SSE pipeline."""
    settings = get_settings()
    default_model = settings.harness_default_model
    light = req.light_model or default_model
    heavy = req.heavy_model or default_model
    if not heavy:
        raise HTTPException(status_code=400, detail="HARNESS_DEFAULT_MODEL not configured")

    llm = LLMClient()
    engine = WorkflowEngine()

    async def event_stream():
        try:
            yield {"event": "intent_planning", "data": json.dumps({"message": "L3 IntentRouter starting"})}
            ir = IntentRouter(llm, light_model=light, heavy_model=heavy)
            plan = await ir.plan(req.sop)
            yield {
                "event": "intent_done",
                "data": plan.model_dump_json(),
            }
            if not plan.spec_valid:
                yield {
                    "event": "abort",
                    "data": json.dumps({
                        "message": "spec invalid; not running",
                        "errors": plan.validation_errors,
                    }),
                }
                return
            async for ev in engine.run(plan.workflow_spec, dict(req.inputs)):
                if isinstance(ev, WorkflowEvent):
                    yield {"event": ev.kind, "data": ev.model_dump_json()}
                elif isinstance(ev, WorkflowRunResult):
                    yield {"event": "result", "data": ev.model_dump_json()}
        finally:
            await llm.aclose()

    return EventSourceResponse(event_stream())
