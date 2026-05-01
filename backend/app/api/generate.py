from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict
from sse_starlette.sse import EventSourceResponse

from app.config import get_settings
from app.core.llm.client import LLMClient
from app.core.pipelines.ui.orchestrator import UIPipeline
from app.core.stability.contracts import UIBlueprint

router = APIRouter(prefix="/api/generate", tags=["generate"])


class GenerateRequest(BaseModel):
    """Request payload for UI generation."""

    blueprint: UIBlueprint
    model: str | None = None

    model_config = ConfigDict(extra="forbid")


@router.post("/ui")
async def generate_ui(req: GenerateRequest) -> EventSourceResponse:
    """SSE stream: phase2 -> phase3 -> completed(with CodeArtifact)."""
    model = req.model or get_settings().harness_default_model
    if not model:
        raise HTTPException(status_code=400, detail="A model must be provided.")

    llm = LLMClient()
    pipeline = UIPipeline(llm, model=model)

    async def event_stream() -> object:
        try:
            async for event in pipeline.run(req.blueprint):
                yield {"event": event.phase, "data": event.model_dump_json()}
        finally:
            await llm.aclose()

    return EventSourceResponse(event_stream())
