"""State machine engine - pure-logic wrapper around state.py transition function.

Holds no side effects; persistence + event emission happen at the impl.py
layer above. This separation keeps the transition logic 100% testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.core.orchestrator.factory_session.state import (
    FactorySessionState,
    next_state_after,
)


class StateMachineError(Exception):
    """Base for state machine errors."""


class StateTransitionError(StateMachineError):
    """Illegal transition attempt."""


@dataclass
class TransitionLogEntry:
    from_state: FactorySessionState
    to_state: FactorySessionState
    event: str
    payload: dict | None = None


@dataclass
class FactorySessionMachine:
    """In-memory state holder.

    persistence layer mirrors this to SQLite; this class itself does not
    touch DB or trace bus.
    """

    state: FactorySessionState = FactorySessionState.CREATED
    history: list[TransitionLogEntry] = field(default_factory=list)
    final_run_ids: dict[str, str] = field(default_factory=dict)
    """Per-stage final_run_id (v2 audit gap #3): the run that's authoritative
    after possible redos. Updated on each stage_done event."""

    def fire(
        self,
        event: Literal["start", "stage_done", "pass", "edit", "redo", "cancel", "fail"],
        payload: dict | None = None,
    ) -> FactorySessionState:
        new_state = next_state_after(self.state, event)
        self.history.append(
            TransitionLogEntry(
                from_state=self.state,
                to_state=new_state,
                event=event,
                payload=payload,
            )
        )
        self.state = new_state
        return new_state


def transition(
    state: FactorySessionState,
    event: Literal["start", "stage_done", "pass", "edit", "redo", "cancel", "fail"],
) -> FactorySessionState:
    """Stateless helper for tests."""
    return next_state_after(state, event)
