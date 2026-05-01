from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from app.config import get_settings
from app.core.llm.client import LLMClient
from app.core.orchestrator.blueprint_planner import BlueprintPlanner
from app.core.stability.contracts import RequirementSpec, UIBlueprint

router = APIRouter(prefix="/api/plan", tags=["plan"])


class PlanRequest(BaseModel):
    spec: RequirementSpec
    model: str | None = None

    model_config = ConfigDict(extra="forbid")


class PlanResponse(BaseModel):
    blueprint: UIBlueprint
    fallback_used: bool
    attempts: int


@router.post("/blueprint")
async def plan_blueprint(req: PlanRequest) -> PlanResponse:
    """Translate a RequirementSpec into a UIBlueprint via LLM + L4 Repair."""
    model = req.model or get_settings().harness_default_model
    if not model:
        raise HTTPException(status_code=400, detail="No model configured. Set HARNESS_DEFAULT_MODEL or pass 'model'.")
    llm = LLMClient()
    try:
        planner = BlueprintPlanner(llm, model=model)
        result = await planner.plan(req.spec)
    finally:
        await llm.aclose()
    return PlanResponse(
        blueprint=result.value,
        fallback_used=result.used_fallback,
        attempts=result.attempts,
    )
