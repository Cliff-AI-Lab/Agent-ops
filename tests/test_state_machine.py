from __future__ import annotations

import pytest

from app.core.dialog.state_machine import DialogState, InvalidTransition, transition


def test_all_valid_transitions_pass() -> None:
    assert transition(DialogState.COLLECTING, DialogState.CLARIFYING) == DialogState.CLARIFYING
    assert transition(DialogState.COLLECTING, DialogState.CONFIRMING) == DialogState.CONFIRMING
    assert transition(DialogState.CLARIFYING, DialogState.CLARIFYING) == DialogState.CLARIFYING
    assert transition(DialogState.CLARIFYING, DialogState.CONFIRMING) == DialogState.CONFIRMING
    assert transition(DialogState.CONFIRMING, DialogState.CLARIFYING) == DialogState.CLARIFYING
    assert transition(DialogState.CONFIRMING, DialogState.GENERATING) == DialogState.GENERATING
    assert transition(DialogState.GENERATING, DialogState.DELIVERED) == DialogState.DELIVERED


def test_invalid_transition_raises() -> None:
    with pytest.raises(InvalidTransition):
        transition(DialogState.COLLECTING, DialogState.DELIVERED)


def test_delivered_has_no_follow_up_transition() -> None:
    with pytest.raises(InvalidTransition):
        transition(DialogState.DELIVERED, DialogState.COLLECTING)
