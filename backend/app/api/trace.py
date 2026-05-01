from __future__ import annotations

from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

from app.core.trace.bus import bus

router = APIRouter(prefix="/api/trace", tags=["trace"])


@router.get("/stream")
async def trace_stream() -> EventSourceResponse:
    """SSE stream of live harness trace events (with buffered replay)."""

    async def event_source():
        async for event in bus.subscribe():
            yield {"data": event.model_dump_json()}

    return EventSourceResponse(event_source())
