"""State enum + transition table for FactorySession.

Per [[Phase-2-架构图]] state diagram:
  6 working states + 6 gate states + 3 terminal states = 15 total
"""

from __future__ import annotations

from enum import Enum
from typing import Literal


class FactorySessionState(str, Enum):
    """All session states."""

    CREATED = "CREATED"

    # 6 working stages (work in progress)
    DESIGNING = "DESIGNING"
    WRAPPING = "WRAPPING"
    ASSEMBLING = "ASSEMBLING"
    TESTING = "TESTING"
    UI_STYLING = "UI_STYLING"
    DEPLOYING = "DEPLOYING"

    # 6 gates (paused waiting for user decision)
    GATE_DESIGN = "GATE_DESIGN"
    GATE_WRAP = "GATE_WRAP"
    GATE_ASSEMBLE = "GATE_ASSEMBLE"
    GATE_TEST = "GATE_TEST"
    GATE_UI = "GATE_UI"
    GATE_DEPLOY = "GATE_DEPLOY"

    # Terminal
    RELEASED = "RELEASED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class Stage(str, Enum):
    """Stage label (used for Trace events + Gate identifiers)."""

    DESIGN = "design"
    WRAP = "wrap"
    ASSEMBLE = "assemble"
    TEST = "test"
    UI = "ui"
    DEPLOY = "deploy"


GateId = Stage  # Gate IDs share the Stage enum


class GateDecision(str, Enum):
    """User decision at a Gate."""

    PASS = "pass"
    EDIT = "edit"  # accepted with patch
    REDO = "redo"  # rerun the same stage


_GATE_STATES: set[FactorySessionState] = {
    FactorySessionState.GATE_DESIGN,
    FactorySessionState.GATE_WRAP,
    FactorySessionState.GATE_ASSEMBLE,
    FactorySessionState.GATE_TEST,
    FactorySessionState.GATE_UI,
    FactorySessionState.GATE_DEPLOY,
}


_STAGE_OF_STATE: dict[FactorySessionState, Stage] = {
    FactorySessionState.DESIGNING: Stage.DESIGN,
    FactorySessionState.GATE_DESIGN: Stage.DESIGN,
    FactorySessionState.WRAPPING: Stage.WRAP,
    FactorySessionState.GATE_WRAP: Stage.WRAP,
    FactorySessionState.ASSEMBLING: Stage.ASSEMBLE,
    FactorySessionState.GATE_ASSEMBLE: Stage.ASSEMBLE,
    FactorySessionState.TESTING: Stage.TEST,
    FactorySessionState.GATE_TEST: Stage.TEST,
    FactorySessionState.UI_STYLING: Stage.UI,
    FactorySessionState.GATE_UI: Stage.UI,
    FactorySessionState.DEPLOYING: Stage.DEPLOY,
    FactorySessionState.GATE_DEPLOY: Stage.DEPLOY,
}


# State after a gate's "pass" decision.
_PASS_NEXT: dict[FactorySessionState, FactorySessionState] = {
    FactorySessionState.GATE_DESIGN: FactorySessionState.WRAPPING,
    FactorySessionState.GATE_WRAP: FactorySessionState.ASSEMBLING,
    FactorySessionState.GATE_ASSEMBLE: FactorySessionState.TESTING,
    FactorySessionState.GATE_TEST: FactorySessionState.UI_STYLING,
    FactorySessionState.GATE_UI: FactorySessionState.DEPLOYING,
    FactorySessionState.GATE_DEPLOY: FactorySessionState.RELEASED,
}


# State after a gate's "redo" decision (back to the same working stage).
_REDO_NEXT: dict[FactorySessionState, FactorySessionState] = {
    FactorySessionState.GATE_DESIGN: FactorySessionState.DESIGNING,
    FactorySessionState.GATE_WRAP: FactorySessionState.WRAPPING,
    FactorySessionState.GATE_ASSEMBLE: FactorySessionState.ASSEMBLING,
    FactorySessionState.GATE_TEST: FactorySessionState.TESTING,
    FactorySessionState.GATE_UI: FactorySessionState.UI_STYLING,
    FactorySessionState.GATE_DEPLOY: FactorySessionState.DEPLOYING,
}


# Auto-transition when a working stage finishes -> enters its gate.
_AUTO_GATE: dict[FactorySessionState, FactorySessionState] = {
    FactorySessionState.DESIGNING: FactorySessionState.GATE_DESIGN,
    FactorySessionState.WRAPPING: FactorySessionState.GATE_WRAP,
    FactorySessionState.ASSEMBLING: FactorySessionState.GATE_ASSEMBLE,
    FactorySessionState.TESTING: FactorySessionState.GATE_TEST,
    FactorySessionState.UI_STYLING: FactorySessionState.GATE_UI,
    FactorySessionState.DEPLOYING: FactorySessionState.GATE_DEPLOY,
}


_TERMINAL: set[FactorySessionState] = {
    FactorySessionState.RELEASED,
    FactorySessionState.CANCELLED,
    FactorySessionState.FAILED,
}


def is_gate(state: FactorySessionState) -> bool:
    return state in _GATE_STATES


def is_terminal(state: FactorySessionState) -> bool:
    return state in _TERMINAL


def stage_for_state(state: FactorySessionState) -> Stage | None:
    """Return the Stage label for any working/gate state, else None."""
    return _STAGE_OF_STATE.get(state)


def next_state_after(
    state: FactorySessionState,
    event: Literal["start", "stage_done", "pass", "edit", "redo", "cancel", "fail"],
) -> FactorySessionState:
    """Pure transition function. Raises StateTransitionError for illegal moves.

    See [[Phase-2-架构图]] § state diagram for the full graph.
    """
    from app.core.orchestrator.factory_session.machine import StateTransitionError

    if event == "cancel":
        if is_terminal(state):
            raise StateTransitionError(
                f"cannot cancel from terminal state {state.value}"
            )
        return FactorySessionState.CANCELLED

    if event == "fail":
        return FactorySessionState.FAILED

    if event == "start":
        if state != FactorySessionState.CREATED:
            raise StateTransitionError(
                f"cannot start from {state.value}; only CREATED is startable"
            )
        return FactorySessionState.DESIGNING

    if event == "stage_done":
        if state not in _AUTO_GATE:
            raise StateTransitionError(
                f"stage_done only valid in working states; got {state.value}"
            )
        return _AUTO_GATE[state]

    if event == "pass":
        if state not in _PASS_NEXT:
            raise StateTransitionError(
                f"pass only valid at a Gate; got {state.value}"
            )
        return _PASS_NEXT[state]

    if event == "edit":
        # edit accepts the patch and treats as pass
        if state not in _PASS_NEXT:
            raise StateTransitionError(
                f"edit only valid at a Gate; got {state.value}"
            )
        return _PASS_NEXT[state]

    if event == "redo":
        if state not in _REDO_NEXT:
            raise StateTransitionError(
                f"redo only valid at a Gate; got {state.value}"
            )
        return _REDO_NEXT[state]

    raise StateTransitionError(f"unknown event: {event}")
