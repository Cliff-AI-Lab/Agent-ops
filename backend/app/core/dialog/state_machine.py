from __future__ import annotations

from enum import Enum


class DialogState(str, Enum):
    """Supported dialog states for the receptionist flow."""

    COLLECTING = "collecting"
    CLARIFYING = "clarifying"
    CONFIRMING = "confirming"
    GENERATING = "generating"
    DELIVERED = "delivered"


VALID_TRANSITIONS: dict[DialogState, set[DialogState]] = {
    DialogState.COLLECTING: {DialogState.CLARIFYING, DialogState.CONFIRMING},
    DialogState.CLARIFYING: {DialogState.CLARIFYING, DialogState.CONFIRMING},
    DialogState.CONFIRMING: {DialogState.CLARIFYING, DialogState.GENERATING},
    DialogState.GENERATING: {DialogState.DELIVERED},
    DialogState.DELIVERED: set(),
}


class InvalidTransition(Exception):
    """Raised when a dialog state transition is invalid."""


def transition(current: DialogState, target: DialogState) -> DialogState:
    """Raise InvalidTransition if target not allowed."""
    if target not in VALID_TRANSITIONS[current]:
        raise InvalidTransition(f"Cannot transition from {current.value} to {target.value}.")
    return target
