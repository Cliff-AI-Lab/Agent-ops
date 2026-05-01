"""Marketplace / Asset Hub API (Batch X).

GET  /api/assets                       — list (filter: status / product_type / session)
GET  /api/assets/stats                 — per-status counts
GET  /api/assets/{id}                  — detail
GET  /api/assets/{id}/download         — return the project.zip
POST /api/assets/{id}/promote          — draft → active + reload generated agents
POST /api/assets/{id}/archive          — withdraw from registry
POST /api/assets/reload                — rescan agents/__generated__/ for promoted entries
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from app.marketplace.asset_store import default_assets_root, get_asset_store
from app.marketplace.auto_register import reload_generated_agents
from app.ontology import GeneratedAsset

router = APIRouter(prefix="/api/assets", tags=["marketplace"])


class PromoteRequest(BaseModel):
    promoted_by: str = Field(default="user")
    model_config = ConfigDict(extra="forbid")


@router.get("")
async def list_assets(
    status: str | None = Query(default=None),
    product_type: str | None = Query(default=None),
    session_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, object]:
    """List GeneratedAssets, newest first."""
    assets = get_asset_store().list(
        status=status, product_type=product_type, session_id=session_id, limit=limit
    )
    return {"total": len(assets), "assets": [a.model_dump(mode="json") for a in assets]}


@router.get("/stats")
async def asset_stats() -> dict[str, int]:
    return get_asset_store().stats()


@router.get("/{asset_id}")
async def get_asset(asset_id: str) -> GeneratedAsset:
    a = get_asset_store().get(asset_id)
    if a is None:
        raise HTTPException(status_code=404, detail=f"asset '{asset_id}' not found")
    return a


@router.get("/{asset_id}/download")
async def download_asset(asset_id: str) -> FileResponse:
    a = get_asset_store().get(asset_id)
    if a is None:
        raise HTTPException(status_code=404, detail=f"asset '{asset_id}' not found")
    # artifact_path is stored relative to project root (parent of assets/)
    project_root = default_assets_root().parent
    full = project_root / a.artifact_path
    if not full.is_file():
        raise HTTPException(status_code=410, detail="artifact zip is gone from disk")
    get_asset_store().increment_usage(asset_id)
    return FileResponse(
        path=str(full),
        media_type="application/zip",
        filename=f"{asset_id}.zip",
    )


@router.post("/{asset_id}/promote")
async def promote_asset(asset_id: str, req: PromoteRequest | None = None) -> dict[str, object]:
    """Promote draft → active. Generated agent becomes IntentRouter-discoverable."""
    a = get_asset_store().promote(asset_id, promoted_by=(req.promoted_by if req else "user"))
    if a is None:
        raise HTTPException(status_code=404, detail=f"asset '{asset_id}' not found")
    n = reload_generated_agents()
    return {"asset": a.model_dump(mode="json"), "active_in_registry": n}


@router.post("/{asset_id}/archive")
async def archive_asset(asset_id: str) -> GeneratedAsset:
    a = get_asset_store().archive(asset_id)
    if a is None:
        raise HTTPException(status_code=404, detail=f"asset '{asset_id}' not found")
    reload_generated_agents()  # cull archived ones
    return a


@router.post("/reload")
async def reload_registry() -> dict[str, int]:
    """Manually rescan agents/__generated__/ and refresh the AgentStore."""
    n = reload_generated_agents()
    return {"reloaded": n}
