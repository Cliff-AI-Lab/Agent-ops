"""Registry REST API — blueprint M2."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.registry.store import get_store

router = APIRouter(prefix="/api/registry", tags=["registry"])


@router.get("/capabilities")
async def list_capabilities(
    activation_status: str | None = Query(default=None, description="Filter by status."),
) -> dict[str, object]:
    """List all registered CapabilityContracts (optionally filter by activation_status)."""
    store = get_store()
    contracts = store.list(activation_status)
    return {
        "total": len(contracts),
        "capabilities": [c.model_dump() for c in contracts],
    }


@router.get("/capabilities/{capability_id}")
async def get_capability(
    capability_id: str,
    version: str | None = Query(default=None),
) -> dict[str, object]:
    """Fetch one capability; if version omitted, returns latest active."""
    store = get_store()
    contract = store.get(capability_id, version)
    if contract is None:
        raise HTTPException(status_code=404, detail=f"capability '{capability_id}' not found")
    return contract.model_dump()


@router.get("/capabilities/{capability_id}/versions")
async def list_versions(capability_id: str) -> dict[str, object]:
    """List all known versions of a capability."""
    versions = get_store().versions(capability_id)
    if not versions:
        raise HTTPException(status_code=404, detail=f"capability '{capability_id}' not found")
    return {"capability_id": capability_id, "versions": versions}
