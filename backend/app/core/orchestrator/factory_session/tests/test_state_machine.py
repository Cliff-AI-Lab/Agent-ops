"""State machine tests - cover all legal transitions + illegal-transition errors."""

from __future__ import annotations

import pytest

from app.core.orchestrator.factory_session import (
    FactorySessionState,
    GateDecision,
    Stage,
    StateTransitionError,
    is_gate,
    next_state_after,
    stage_for_state,
    transition,
)
from app.core.orchestrator.factory_session.machine import FactorySessionMachine


# -- Pure transition function ---------------------------------------------


def test_start_from_created():
    assert transition(FactorySessionState.CREATED, "start") == FactorySessionState.DESIGNING


def test_start_from_designing_illegal():
    with pytest.raises(StateTransitionError):
        transition(FactorySessionState.DESIGNING, "start")


def test_stage_done_designing_to_gate():
    assert transition(FactorySessionState.DESIGNING, "stage_done") == FactorySessionState.GATE_DESIGN


def test_stage_done_at_gate_illegal():
    with pytest.raises(StateTransitionError):
        transition(FactorySessionState.GATE_DESIGN, "stage_done")


def test_pass_progresses_through_all_six_gates():
    s = FactorySessionState.GATE_DESIGN
    assert transition(s, "pass") == FactorySessionState.WRAPPING
    assert transition(FactorySessionState.GATE_WRAP, "pass") == FactorySessionState.ASSEMBLING
    assert transition(FactorySessionState.GATE_ASSEMBLE, "pass") == FactorySessionState.TESTING
    assert transition(FactorySessionState.GATE_TEST, "pass") == FactorySessionState.UI_STYLING
    assert transition(FactorySessionState.GATE_UI, "pass") == FactorySessionState.DEPLOYING
    assert transition(FactorySessionState.GATE_DEPLOY, "pass") == FactorySessionState.RELEASED


def test_edit_behaves_like_pass():
    """edit accepts the patch then advances, same as pass."""
    assert transition(FactorySessionState.GATE_TEST, "edit") == FactorySessionState.UI_STYLING


def test_redo_returns_to_same_stage():
    assert transition(FactorySessionState.GATE_DESIGN, "redo") == FactorySessionState.DESIGNING
    assert transition(FactorySessionState.GATE_TEST, "redo") == FactorySessionState.TESTING
    assert transition(FactorySessionState.GATE_DEPLOY, "redo") == FactorySessionState.DEPLOYING


def test_pass_at_working_state_illegal():
    with pytest.raises(StateTransitionError):
        transition(FactorySessionState.DESIGNING, "pass")


def test_redo_at_working_state_illegal():
    with pytest.raises(StateTransitionError):
        transition(FactorySessionState.TESTING, "redo")


def test_cancel_from_any_non_terminal():
    for s in [
        FactorySessionState.CREATED,
        FactorySessionState.DESIGNING,
        FactorySessionState.GATE_DESIGN,
        FactorySessionState.ASSEMBLING,
        FactorySessionState.UI_STYLING,
    ]:
        assert transition(s, "cancel") == FactorySessionState.CANCELLED


def test_cancel_from_terminal_illegal():
    for s in [
        FactorySessionState.RELEASED,
        FactorySessionState.CANCELLED,
        FactorySessionState.FAILED,
    ]:
        with pytest.raises(StateTransitionError):
            transition(s, "cancel")


def test_fail_from_anywhere():
    assert transition(FactorySessionState.DESIGNING, "fail") == FactorySessionState.FAILED
    assert transition(FactorySessionState.GATE_TEST, "fail") == FactorySessionState.FAILED


def test_unknown_event():
    with pytest.raises(StateTransitionError):
        transition(FactorySessionState.CREATED, "warp_drive")  # type: ignore[arg-type]


# -- Helpers --------------------------------------------------------------


def test_is_gate():
    assert is_gate(FactorySessionState.GATE_DESIGN)
    assert is_gate(FactorySessionState.GATE_DEPLOY)
    assert not is_gate(FactorySessionState.DESIGNING)
    assert not is_gate(FactorySessionState.RELEASED)


def test_stage_for_state():
    assert stage_for_state(FactorySessionState.DESIGNING) == Stage.DESIGN
    assert stage_for_state(FactorySessionState.GATE_DESIGN) == Stage.DESIGN
    assert stage_for_state(FactorySessionState.UI_STYLING) == Stage.UI
    assert stage_for_state(FactorySessionState.GATE_DEPLOY) == Stage.DEPLOY
    assert stage_for_state(FactorySessionState.RELEASED) is None
    assert stage_for_state(FactorySessionState.CREATED) is None


# -- FactorySessionMachine ------------------------------------------------


def test_machine_full_happy_path():
    """Full happy path through all 6 gates with pass at each."""
    m = FactorySessionMachine()
    assert m.state == FactorySessionState.CREATED

    m.fire("start")
    assert m.state == FactorySessionState.DESIGNING

    for _ in range(6):
        m.fire("stage_done")
        assert is_gate(m.state)
        m.fire("pass")

    assert m.state == FactorySessionState.RELEASED
    # 1 start + 6 stage_done + 6 pass = 13 transitions
    assert len(m.history) == 13


def test_machine_redo_loops_back():
    """Redo at GATE_TEST goes back to TESTING; passes through again."""
    m = FactorySessionMachine()
    m.fire("start")
    # advance to GATE_TEST: 3 full pass-throughs + one stage_done
    for _ in range(3):
        m.fire("stage_done")
        m.fire("pass")
    m.fire("stage_done")
    assert m.state == FactorySessionState.GATE_TEST

    m.fire("redo")
    assert m.state == FactorySessionState.TESTING

    m.fire("stage_done")
    assert m.state == FactorySessionState.GATE_TEST

    m.fire("pass")
    assert m.state == FactorySessionState.UI_STYLING


def test_machine_cancel_from_middle():
    m = FactorySessionMachine()
    m.fire("start")
    m.fire("stage_done")
    m.fire("pass")
    m.fire("stage_done")
    assert m.state == FactorySessionState.GATE_WRAP
    m.fire("cancel")
    assert m.state == FactorySessionState.CANCELLED

    with pytest.raises(StateTransitionError):
        m.fire("cancel")


def test_machine_payload_preserved_in_history():
    m = FactorySessionMachine()
    m.fire("start")
    m.fire("stage_done")
    m.fire("redo", payload={"reason": "user wants different atoms"})
    last = m.history[-1]
    assert last.event == "redo"
    assert last.payload == {"reason": "user wants different atoms"}
