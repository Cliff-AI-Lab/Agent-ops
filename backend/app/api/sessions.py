from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from app.config import get_settings
from app.core.dialog.receptionist import Receptionist
from app.core.dialog.session import SessionStore
from app.core.dialog.turn_orchestrator import TurnOrchestrator
from app.core.llm.client import LLMClient

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


class CreateSessionRequest(BaseModel):
    """Request body for creating a new session."""

    sop: str


class TurnRequest(BaseModel):
    """Request body for a dialog turn."""

    message: str


def _db_path() -> str:
    return str(Path(__file__).resolve().parents[3] / "harness.db")


def _default_model() -> str:
    settings = get_settings()
    return settings.harness_default_model or "gpt-4o-mini"


@router.post("")
async def create_session(payload: CreateSessionRequest) -> dict[str, str]:
    """Create a new receptionist session."""
    llm = LLMClient()
    try:
        receptionist = Receptionist(llm, SessionStore(_db_path()), _default_model())
        session = await receptionist.start(payload.sop)
    finally:
        await llm.aclose()
    return {"session_id": session.id, "state": session.state.value}


@router.post("/{session_id}/turn")
async def turn_session(
    session_id: str,
    payload: TurnRequest,
    triage: bool = Query(default=True, description="When true (default), Triage decides routing."),
) -> EventSourceResponse:
    """Stream one dialog turn as SSE.

    Default behavior (triage=true, Batch I): every message is first routed via
    :class:`TurnOrchestrator` so the user can call generated agents / planner /
    help directly inline. Set ``?triage=false`` to bypass and stay in pure
    receptionist mode (back-compat for older clients / tests).
    """
    llm = LLMClient()
    store = SessionStore(_db_path())

    async def event_stream():
        try:
            if triage:
                orchestrator = TurnOrchestrator(
                    llm, store, default_model=_default_model(),
                )
                async for event in orchestrator.turn(session_id, payload.message):
                    yield {"data": json.dumps(event, ensure_ascii=False, default=str)}
            else:
                receptionist = Receptionist(llm, store, _default_model())
                async for event in receptionist.turn(session_id, payload.message):
                    yield {"data": json.dumps(event, ensure_ascii=False)}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Session not found.") from exc
        finally:
            await llm.aclose()

    return EventSourceResponse(event_stream())


@router.get("/{session_id}")
async def get_session(session_id: str) -> dict[str, object]:
    """Return the current session snapshot."""
    store = SessionStore(_db_path())
    session = await store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    return session.model_dump(mode="json")
