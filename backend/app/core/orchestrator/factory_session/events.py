"""Canvas + Gate trace event emitters for FactorySession.

Wraps the existing app.core.trace.bus.emit() with factory.* event types so
SSE consumers (Phase 3 World canvas) can subscribe to a stable namespace.

Event taxonomy (per [[Phase-2-架构图]] § Trace events):
  factory.session.created
  factory.stage.entered
  factory.stage.completed
  factory.canvas.node_added
  factory.canvas.node_updated
  factory.canvas.edge_added
  factory.gate.open
  factory.gate.decision
  factory.gate.closed
  factory.session.released
  factory.session.cancelled
  factory.session.failed
"""

from __future__ import annotations

from typing import Any

from app.core.trace.bus import emit


_COMPONENT = "FactorySession"


def session_created(session_id: str, nl: str) -> None:
    emit(
        "L3",
        _COMPONENT,
        "factory.session.created",
        f"sid={session_id[:8]} nl_len={len(nl)}",
        data={"session_id": session_id, "nl_preview": nl[:100]},
    )


def stage_entered(session_id: str, stage: str) -> None:
    emit(
        "L3",
        _COMPONENT,
        "factory.stage.entered",
        f"sid={session_id[:8]} stage={stage}",
        data={"session_id": session_id, "stage": stage},
    )


def stage_completed(session_id: str, stage: str, run_id: str) -> None:
    emit(
        "L3",
        _COMPONENT,
        "factory.stage.completed",
        f"sid={session_id[:8]} stage={stage} run={run_id}",
        data={"session_id": session_id, "stage": stage, "run_id": run_id},
    )


def canvas_node_added(
    session_id: str, node_id: str, asset_id: str, subcategory: str
) -> None:
    emit(
        "L3",
        _COMPONENT,
        "factory.canvas.node_added",
        f"sid={session_id[:8]} node={node_id} atom={asset_id}",
        data={
            "session_id": session_id,
            "node_id": node_id,
            "asset_id": asset_id,
            "subcategory": subcategory,
        },
    )


def canvas_edge_added(
    session_id: str,
    from_node: str,
    to_node: str,
    type_check: dict[str, str],
    needs_adapter: bool,
) -> None:
    emit(
        "L3",
        _COMPONENT,
        "factory.canvas.edge_added",
        f"sid={session_id[:8]} {from_node}->{to_node}",
        data={
            "session_id": session_id,
            "from_node": from_node,
            "to_node": to_node,
            "type_check": type_check,
            "needs_adapter": needs_adapter,
        },
    )


def gate_open(
    session_id: str,
    gate_id: str,
    ai_report: dict[str, Any],
    checklist: list[str],
) -> None:
    emit(
        "L3",
        _COMPONENT,
        "factory.gate.open",
        f"sid={session_id[:8]} gate={gate_id}",
        data={
            "session_id": session_id,
            "gate_id": gate_id,
            "ai_report": ai_report,
            "checklist": checklist,
        },
    )


def gate_decision(
    session_id: str, gate_id: str, decision: str, payload: dict | None = None
) -> None:
    emit(
        "L3",
        _COMPONENT,
        "factory.gate.decision",
        f"sid={session_id[:8]} gate={gate_id} decision={decision}",
        data={
            "session_id": session_id,
            "gate_id": gate_id,
            "decision": decision,
            "payload": payload,
        },
    )


def gate_closed(session_id: str, gate_id: str) -> None:
    emit(
        "L3",
        _COMPONENT,
        "factory.gate.closed",
        f"sid={session_id[:8]} gate={gate_id}",
        data={"session_id": session_id, "gate_id": gate_id},
    )


def session_released(session_id: str, artifact_path: str | None = None) -> None:
    emit(
        "L3",
        _COMPONENT,
        "factory.session.released",
        f"sid={session_id[:8]} artifact={artifact_path or '-'}",
        data={"session_id": session_id, "artifact_path": artifact_path},
    )


def session_cancelled(session_id: str, reason: str = "user") -> None:
    emit(
        "L3",
        _COMPONENT,
        "factory.session.cancelled",
        f"sid={session_id[:8]} reason={reason}",
        data={"session_id": session_id, "reason": reason},
    )


def session_failed(session_id: str, error: str) -> None:
    emit(
        "L4",
        _COMPONENT,
        "factory.session.failed",
        f"sid={session_id[:8]} err={error[:120]}",
        data={"session_id": session_id, "error": error},
    )
