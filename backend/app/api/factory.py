"""HTTP API for FactorySession (Phase 2 W5).

Exposes the 6-stage 6-Gate state machine over HTTP for World frontend
(Phase 3) and CLI clients.

Endpoints (per [[Phase-2-架构图]] § HTTP API):
  POST   /api/factory/start            create + auto-start session
  GET    /api/factory/{sid}            current session record
  GET    /api/factory/{sid}/events     SSE event stream (filtered by sid)
  POST   /api/factory/{sid}/gate       apply gate decision (pass/edit/redo)
  POST   /api/factory/{sid}/cancel     cancel session
  GET    /api/factory/{sid}/artifact   final DSL output (after RELEASED)
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from app.config import get_settings
from app.core.orchestrator.factory_session import FactorySessionState
from app.core.orchestrator.factory_session.impl import FactorySessionImpl
from app.core.orchestrator.factory_session.persistence import SessionPersistence
from app.core.pipelines.factory.pipeline import FactoryPipeline
from app.core.trace.bus import bus

router = APIRouter(prefix="/api/factory", tags=["factory"])


def _atoms_dir() -> Path:
    here = Path(__file__).resolve()
    repo_root = here.parents[3]
    return repo_root / "capabilities" / "atom"


def _persistence() -> SessionPersistence:
    return SessionPersistence(db_path="harness.db")


def _build_pipeline() -> FactoryPipeline:
    return FactoryPipeline(atoms_dir=_atoms_dir())


# ---- request/response models ---------------------------------------------


class StartRequest(BaseModel):
    nl: str
    industry_code: str | None = None
    scenario: str | None = None


class StartResponse(BaseModel):
    session_id: str
    state: str


class GateDecisionRequest(BaseModel):
    decision: Literal["pass", "edit", "redo"]
    payload: dict | None = None
    decided_by: str | None = None


class SessionResponse(BaseModel):
    session_id: str
    state: str
    nl: str
    final_artifact_path: str | None = None


# ---- background runner ---------------------------------------------------


async def _drive_to_next_gate(session_id: str) -> None:
    """Background task: advance one stage past CREATED or current working state."""
    persistence = _persistence()
    session = await FactorySessionImpl.load(
        pipeline=_build_pipeline(), persistence=persistence, session_id=session_id
    )
    if session is None:
        return
    try:
        await session.run_next_stage()
    except Exception:
        # Errors already emit factory.session.failed via events.py
        pass


# ---- routes --------------------------------------------------------------


@router.post("/start", response_model=StartResponse)
async def start(req: StartRequest, bg: BackgroundTasks) -> StartResponse:
    """Create a session and kick off the design stage in the background."""
    persistence = _persistence()
    session = await FactorySessionImpl.create(
        pipeline=_build_pipeline(),
        persistence=persistence,
        nl=req.nl,
        industry_code=req.industry_code,
        scenario=req.scenario,
    )
    bg.add_task(_drive_to_next_gate, session.session_id)
    return StartResponse(session_id=session.session_id, state=session.state.value)


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(session_id: str) -> SessionResponse:
    record = await _persistence().get(session_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"session {session_id} not found")
    return SessionResponse(
        session_id=record.session_id,
        state=record.state.value,
        nl=record.nl,
        final_artifact_path=record.final_artifact_path,
    )


@router.get("/{session_id}/events")
async def events(session_id: str) -> EventSourceResponse:
    """SSE stream filtered to events for this session_id.

    Includes existing trace events (factory.* etc.) AND a synthetic close event
    when the session reaches a terminal state.
    """

    async def event_source():
        async for event in bus.subscribe():
            ev_sid = (event.data or {}).get("session_id") if event.data else None
            if ev_sid is None or ev_sid == session_id:
                yield {"data": event.model_dump_json()}

    return EventSourceResponse(event_source())


@router.post("/{session_id}/gate", response_model=SessionResponse)
async def apply_gate(
    session_id: str, req: GateDecisionRequest, bg: BackgroundTasks
) -> SessionResponse:
    persistence = _persistence()
    session = await FactorySessionImpl.load(
        pipeline=_build_pipeline(), persistence=persistence, session_id=session_id
    )
    if session is None:
        raise HTTPException(status_code=404, detail=f"session {session_id} not found")
    try:
        new_state = await session.apply_gate_decision(
            decision=req.decision,
            payload=req.payload,
            decided_by=req.decided_by,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # If we transitioned to a working state, drive forward to next gate.
    if new_state not in (
        FactorySessionState.RELEASED,
        FactorySessionState.CANCELLED,
        FactorySessionState.FAILED,
    ):
        bg.add_task(_drive_to_next_gate, session_id)

    record = await persistence.get(session_id)
    assert record is not None
    return SessionResponse(
        session_id=record.session_id,
        state=record.state.value,
        nl=record.nl,
        final_artifact_path=record.final_artifact_path,
    )


@router.post("/{session_id}/cancel", response_model=SessionResponse)
async def cancel_session(session_id: str) -> SessionResponse:
    persistence = _persistence()
    session = await FactorySessionImpl.load(
        pipeline=_build_pipeline(), persistence=persistence, session_id=session_id
    )
    if session is None:
        raise HTTPException(status_code=404, detail=f"session {session_id} not found")
    await session.cancel(reason="api")
    record = await persistence.get(session_id)
    assert record is not None
    return SessionResponse(
        session_id=record.session_id,
        state=record.state.value,
        nl=record.nl,
        final_artifact_path=record.final_artifact_path,
    )


@router.get("/{session_id}/artifact")
async def get_artifact(session_id: str) -> dict:
    record = await _persistence().get(session_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"session {session_id} not found")
    if record.state != FactorySessionState.RELEASED:
        raise HTTPException(
            status_code=409,
            detail=f"session not RELEASED yet (state={record.state.value})",
        )
    if not record.final_artifact_path:
        raise HTTPException(
            status_code=500, detail="RELEASED session missing final_artifact_path"
        )
    path = Path(record.final_artifact_path)
    if not path.exists():
        raise HTTPException(status_code=410, detail=f"artifact file missing: {path}")
    return {
        "session_id": session_id,
        "artifact_path": str(path),
        "content": path.read_text(encoding="utf-8"),
    }


# ---- V2.3 budget summary endpoint ---------------------------------------


@router.get("/budget/{tenant_id}/today")
async def budget_today(tenant_id: str) -> dict:
    """Return today's accumulated spend for a tenant.

    Useful for dashboards / pre-flight checks before issuing a /build call.
    Returns 0 if no entries yet (no error — fresh tenant is fine).
    """
    from app.core.governance import CostLedger

    ledger = CostLedger(db_path=str(_atoms_dir().parent / "harness.db"))
    try:
        spent = await ledger.daily_spent(tenant_id=tenant_id)
        count = await ledger.count(tenant_id=tenant_id)
    except Exception as exc:  # noqa: BLE001
        # Likely missing cost_ledger table (DB not initialized yet).
        raise HTTPException(
            status_code=503,
            detail=f"cost_ledger not available: {exc}",
        ) from exc
    return {
        "tenant_id": tenant_id,
        "spent_today_cny": spent,
        "build_count_total": count,
    }


@router.get("/budget/{tenant_id}/month")
async def budget_month(tenant_id: str) -> dict:
    """Return current month's accumulated spend for a tenant."""
    from app.core.governance import CostLedger

    ledger = CostLedger(db_path=str(_atoms_dir().parent / "harness.db"))
    try:
        spent = await ledger.monthly_spent(tenant_id=tenant_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=503,
            detail=f"cost_ledger not available: {exc}",
        ) from exc
    return {"tenant_id": tenant_id, "spent_month_cny": spent}


# ---- V2.1.0+: Phase 7 stateless build endpoint via Switcher ---------------


class BuildRequest(BaseModel):
    """Request body for /api/factory/build (Phase 7 + V2.2/V2.3 governance)."""

    nl: str
    deploy: bool = False  # if True, multi-agent path writes artifact to disk
    budget_cny: float | None = None  # V2.2: enable pre-flight cost gate when set
    tenant_id: str = "default"  # V2.3: partitions cost ledger entries
    use_tiktoken: bool = True   # V2.3: real token count vs char-length proxy
    soft_warn: bool = True      # V2.3: emit budget_soft_warn at 80% threshold


@router.post("/build")
async def build_auto(req: BuildRequest) -> dict:
    """Stateless NL -> single OR multi-agent system, returns synchronously.

    Unlike /start (which creates a 6-Gate session), this is a one-shot pipeline.
    Routes via FactorySwitcher: single LLM classify decides single vs multi.

    Returns:
      {
        "path": "single" | "multi",
        "classification": {...},
        single path: {goal, target, asset_ids, outputs: {dify, n8n}, validation, ...},
        multi path:  {system_slug, source_code, spec: {...}, deployed_files?: {...}}
      }
    """
    if not req.nl or not req.nl.strip():
        raise HTTPException(status_code=400, detail="nl cannot be empty")

    from app.core.governance import (
        CostBudgetExceeded,
        HeuristicCostEstimator,
        SoftWarnBudget,
        ThresholdCostBudget,
        TiktokenCostEstimator,
    )
    from app.core.pipelines.factory.multi_agent_pipeline import (
        MultiAgentFactoryPipeline,
    )
    from app.core.pipelines.factory.switcher import FactorySwitcher

    estimator = None
    budget = None
    if req.budget_cny is not None:
        if req.budget_cny <= 0:
            raise HTTPException(status_code=400, detail="budget_cny must be positive")
        estimator = (
            TiktokenCostEstimator() if req.use_tiktoken else HeuristicCostEstimator()
        )
        underlying_budget = ThresholdCostBudget(threshold_cny=req.budget_cny)
        budget = (
            SoftWarnBudget(underlying=underlying_budget) if req.soft_warn else underlying_budget
        )

    single = _build_pipeline()
    switcher = FactorySwitcher(
        single_pipeline=single,
        cost_estimator=estimator,
        cost_budget=budget,
        tenant_id=req.tenant_id,
    )
    try:
        result = await switcher.build(req.nl)
    except CostBudgetExceeded as exc:
        # 402 Payment Required — semantically nicer than 400 for budget refusal
        raise HTTPException(status_code=402, detail={
            "error": "cost_budget_exceeded",
            "reason": exc.decision.reason,
            "estimate_cny": exc.decision.estimate.estimated_total_cny,
            "threshold_cny": exc.decision.threshold_cny,
        }) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if req.deploy and result["path"] == "multi":
        deploy_root = _atoms_dir().parent / "agents" / "__generated__" / "multi_agent"
        deploy_root.mkdir(parents=True, exist_ok=True)
        multi = MultiAgentFactoryPipeline()
        paths = multi.deploy(result, deploy_root)
        result["deployed_files"] = {k: str(v) for k, v in paths.items()}

    # Serialize Pydantic objects to dicts for JSON response
    payload: dict = {
        "path": result["path"],
        "classification": result["classification"].model_dump(),
    }
    if result["path"] == "multi":
        payload["system_slug"] = result["system_slug"]
        payload["source_code"] = result["source_code"]
        payload["spec"] = result["spec"].model_dump()
        if "deployed_files" in result:
            payload["deployed_files"] = result["deployed_files"]
    else:
        payload["goal"] = result["intent"].goal
        payload["target"] = result["dag"].target
        payload["node_count"] = len(result["dag"].nodes)
        payload["asset_ids"] = [n.asset_id for n in result["dag"].nodes]
        payload["outputs"] = result.get("outputs", {})
        # Optional: include trigger / validation if present
        if "validation" in result:
            payload["validation"] = result["validation"]
    return payload
