"""Triage API (Batch Z++).

POST /api/triage — first-hop dynamic routing for a single user message.
Returns a TriageDecision audit record without executing the target.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.config import get_settings
from app.core.dialog.session import SessionStore
from app.core.llm.client import LLMClient
from app.core.orchestrator.triage import TriageAgent, TriageRouteResult

router = APIRouter(prefix="/api/triage", tags=["triage"])


class TriageRequest(BaseModel):
    """Body for POST /api/triage."""

    message: str = Field(..., min_length=1, description="The user's incoming message.")
    session_id: str | None = Field(
        default=None,
        description="Optional. If provided, a compact summary is loaded and passed to triage.",
    )
    model: str | None = Field(
        default=None,
        description="Override for the triage light model.",
    )

    model_config = ConfigDict(extra="forbid")


def _db_path() -> str:
    return str(Path(__file__).resolve().parents[3] / "harness.db")


async def _build_session_summary(session_id: str) -> str | None:
    """Compact, audit-friendly summary of a session for triage context."""
    store = SessionStore(_db_path())
    session = await store.get(session_id)
    if session is None:
        return None
    spec = session.requirement_spec or {}
    sop = next((m.content for m in session.messages if m.role == "user"), "")
    return (
        f"session_id={session_id} · state={session.state.value} · "
        f"messages={len(session.messages)} · sop={sop[:120]} · "
        f"product_name={spec.get('product_name', '-')} · "
        f"product_type={spec.get('product_type', '-')}"
    )


@router.post("", response_model=TriageRouteResult)
async def triage(req: TriageRequest) -> TriageRouteResult:
    """Route a single user message. Returns the decision; does NOT execute."""
    settings = get_settings()
    model = req.model or settings.harness_default_model
    if not model:
        raise HTTPException(status_code=400, detail="HARNESS_DEFAULT_MODEL not configured")

    summary: str | None = None
    if req.session_id:
        summary = await _build_session_summary(req.session_id)
        if summary is None:
            raise HTTPException(status_code=404, detail=f"session {req.session_id} not found")

    llm = LLMClient()
    try:
        agent = TriageAgent(llm, model=model)
        result = await agent.route(req.message, session_summary=summary)
    finally:
        await llm.aclose()
    return result
