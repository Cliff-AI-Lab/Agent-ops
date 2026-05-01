"""FactorySession: 6-stage 6-Gate state machine wrapping FactoryPipeline.

Phase 2 of V2.0.0 factory: enables the 'large-node intervention' UX
(per [[决策记录]] core rule #4).

State machine: see state.py + machine.py
Persistence:   see persistence.py (SQLite, 3 new tables)
Events:        see events.py (factory.* trace event types)
"""

from app.core.orchestrator.factory_session.state import (
    FactorySessionState,
    GateId,
    GateDecision,
    Stage,
    is_gate,
    next_state_after,
    stage_for_state,
)
from app.core.orchestrator.factory_session.machine import (
    StateMachineError,
    StateTransitionError,
    transition,
)

__all__ = [
    "FactorySessionState",
    "GateDecision",
    "GateId",
    "Stage",
    "StateMachineError",
    "StateTransitionError",
    "is_gate",
    "next_state_after",
    "stage_for_state",
    "transition",
]
