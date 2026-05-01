from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict
from sse_starlette.sse import EventSourceResponse

from app.config import get_settings
from app.core.dialog.session import SessionStore
from app.core.llm.client import LLMClient
from app.core.orchestrator.deliver_pipeline import DeliverPipeline

router = APIRouter(prefix="/api/sessions", tags=["deliver"])


class DeliverRequest(BaseModel):
    model: str | None = None

    model_config = ConfigDict(extra="forbid")


def _db_path() -> str:
    return str(Path(__file__).resolve().parents[3] / "harness.db")


@router.post("/{session_id}/deliver")
async def deliver(session_id: str, req: DeliverRequest | None = None) -> EventSourceResponse:
    """Run the end-to-end deliver pipeline for a session, streamed via SSE."""
    settings = get_settings()
    model = (req.model if req else None) or settings.harness_default_model
    if not model:
        raise HTTPException(status_code=400, detail="No model configured.")

    store = SessionStore(_db_path())
    session = await store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    if not session.requirement_spec:
        raise HTTPException(status_code=400, detail="session has no requirement_spec")

    llm = LLMClient()
    pipeline = DeliverPipeline(llm, model=model)

    async def event_stream() -> object:
        try:
            async for event in pipeline.run(session):
                yield {"event": event.stage, "data": event.model_dump_json()}
        finally:
            await llm.aclose()

    return EventSourceResponse(event_stream())
