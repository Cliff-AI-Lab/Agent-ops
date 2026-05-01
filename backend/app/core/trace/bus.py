from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import AsyncIterator
from typing import Literal

from pydantic import BaseModel, Field

Layer = Literal["L1", "L2", "L3", "L4", "L5", "L6", "sys"]


class TraceEvent(BaseModel):
    """A single trace event emitted by any layer of the Harness."""

    ts: float = Field(default_factory=time.time)
    layer: Layer = "sys"
    component: str = ""
    kind: str = ""
    message: str = ""
    session_id: str | None = None
    data: dict[str, object] | None = None


class TraceBus:
    """In-memory pub/sub with a ring buffer for replay to new subscribers."""

    def __init__(self, buffer_size: int = 200) -> None:
        self._buffer: deque[TraceEvent] = deque(maxlen=buffer_size)
        self._queues: list[asyncio.Queue[TraceEvent]] = []
        self._lock = asyncio.Lock()

    def publish(self, event: TraceEvent) -> None:
        """Publish an event. Never blocks callers."""
        self._buffer.append(event)
        for q in self._queues:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    async def subscribe(self) -> AsyncIterator[TraceEvent]:
        """Yield events. Replays the current buffer first, then live events."""
        q: asyncio.Queue[TraceEvent] = asyncio.Queue(maxsize=1024)
        async with self._lock:
            for ev in list(self._buffer):
                await q.put(ev)
            self._queues.append(q)
        try:
            while True:
                ev = await q.get()
                yield ev
        finally:
            async with self._lock:
                if q in self._queues:
                    self._queues.remove(q)


bus = TraceBus()


def emit(
    layer: Layer,
    component: str,
    kind: str,
    message: str = "",
    *,
    session_id: str | None = None,
    data: dict[str, object] | None = None,
) -> None:
    """Synchronous helper for publishing a trace event."""
    bus.publish(
        TraceEvent(
            layer=layer,
            component=component,
            kind=kind,
            message=message,
            session_id=session_id,
            data=data,
        )
    )
