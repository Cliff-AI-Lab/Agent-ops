"""IntentRouter API (Batch Z).

POST /api/intent/plan — turn a user SOP into a candidate WorkflowSpec
that can be reviewed before execution.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.config import get_settings
from app.core.llm.client import LLMClient
from app.core.orchestrator.intent_router import IntentRouteResult, IntentRouter

router = APIRouter(prefix="/api/intent", tags=["intent"])


class PlanRequest(BaseModel):
    """Body for POST /api/intent/plan."""

    sop: str = Field(..., min_length=1)
    light_model: str | None = Field(
        default=None,
        description="Override for the intent-extraction model.",
    )
    heavy_model: str | None = Field(
        default=None,
        description="Override for the WorkflowSpec planner model.",
    )

    model_config = ConfigDict(extra="forbid")


@router.post("/plan", response_model=IntentRouteResult)
async def plan(req: PlanRequest) -> IntentRouteResult:
    """Generate a candidate WorkflowSpec for the given SOP. Does NOT execute."""
    settings = get_settings()
    default_model = settings.harness_default_model
    light = req.light_model or default_model
    heavy = req.heavy_model or default_model
    if not heavy:
        raise HTTPException(status_code=400, detail="HARNESS_DEFAULT_MODEL not configured")

    llm = LLMClient()
    try:
        router_obj = IntentRouter(llm, light_model=light, heavy_model=heavy)
        result = await router_obj.plan(req.sop)
    finally:
        await llm.aclose()
    return result
