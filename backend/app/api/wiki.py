"""Wiki API — unified shelf across the three-tier ontology pyramid (Batch Y).

Endpoints (Batch Y):
- GET /api/wiki/all                — every tier in one shot
- GET /api/wiki/agents             — agent tier only
- GET /api/wiki/stats              — per-tier counts
- GET /api/wiki/{kind}/{id}/markdown  — curated README.md (preferred) or generated MD

Endpoints (kept for backward compat from Batch C+ / C++):
- GET /api/wiki/tools / tools/{id} / categories / providers
- GET /api/wiki/presets / presets/{id}
- GET /api/wiki/search             — unified search
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.product_types import ProductType
from app.registry.agent_store import get_agent_store
from app.registry.markdown_gen import markdown_for
from app.registry.preset_store import get_preset_store
from app.registry.registry_hub import EntryKind, list_all
from app.registry.registry_hub import search as hub_search
from app.registry.registry_hub import stats as hub_stats
from app.registry.store import get_store
from app.registry.tool_store import get_tool_store

router = APIRouter(prefix="/api/wiki", tags=["wiki"])


# ============ Batch Y unified-shelf endpoints ============
@router.get("/all")
async def list_all_entries(
    kind: EntryKind | None = Query(default=None),
    activation_status: str | None = Query(default=None),
) -> dict[str, object]:
    """List every registered entry across atom / composite / agent / preset tiers."""
    entries = list_all(kind=kind, activation_status=activation_status)
    return {"total": len(entries), "entries": [e.model_dump() for e in entries]}


@router.get("/stats")
async def list_stats() -> dict[str, object]:
    """Per-tier counts (atom / composite / agent / preset)."""
    return hub_stats()


@router.get("/agents")
async def list_agents(activation_status: str | None = Query(default=None)) -> dict[str, object]:
    """List registered AgentContracts (Batch Y agent tier)."""
    agents = get_agent_store().list(activation_status=activation_status)
    return {"total": len(agents), "agents": [a.model_dump() for a in agents]}


@router.get("/agents/{agent_id}")
async def get_agent(agent_id: str) -> dict[str, object]:
    a = get_agent_store().get(agent_id)
    if a is None:
        raise HTTPException(status_code=404, detail=f"agent '{agent_id}' not found")
    return a.model_dump()


@router.get("/{kind}/{entry_id}/markdown", response_model=None)
async def get_markdown(kind: EntryKind, entry_id: str) -> dict[str, str]:
    """Standardized MD for any registered entry. Curated README beats generated."""
    try:
        md, source = markdown_for(kind, entry_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"kind": kind, "id": entry_id, "source": source, "markdown": md}


@router.get("/tools")
async def list_tools(
    activation_status: str | None = Query(default=None),
    category: str | None = Query(default=None),
    provider: str | None = Query(default=None),
    tag: str | None = Query(default=None),
) -> dict[str, object]:
    """List registered ToolContracts (with optional filters)."""
    tools = get_tool_store().list(
        activation_status=activation_status, category=category, provider=provider, tag=tag
    )
    return {"total": len(tools), "tools": [t.model_dump() for t in tools]}


@router.get("/tools/{tool_id}")
async def get_tool(tool_id: str, version: str | None = Query(default=None)) -> dict[str, object]:
    t = get_tool_store().get(tool_id, version)
    if t is None:
        raise HTTPException(status_code=404, detail=f"tool '{tool_id}' not found")
    return t.model_dump()


@router.get("/categories")
async def list_categories() -> dict[str, object]:
    return {"categories": get_tool_store().categories()}


@router.get("/providers")
async def list_providers() -> dict[str, object]:
    return {"providers": get_tool_store().providers()}


@router.get("/presets")
async def list_presets(
    activation_status: str | None = Query(default=None),
    product_type: ProductType | None = Query(
        default=None,
        description="Filter to bundles whose applies_to contains this product_type.",
    ),
) -> dict[str, object]:
    """List registered PresetBundles (Batch C++)."""
    bundles = get_preset_store().list(activation_status=activation_status)
    if product_type:
        bundles = [b for b in bundles if product_type in b.applies_to]
    return {"total": len(bundles), "presets": [b.model_dump() for b in bundles]}


@router.get("/presets/{bundle_id}")
async def get_preset(bundle_id: str) -> dict[str, object]:
    b = get_preset_store().get(bundle_id)
    if b is None:
        raise HTTPException(status_code=404, detail=f"preset '{bundle_id}' not found")
    return b.model_dump()


@router.get("/search")
async def search(q: str = Query(default="", description="Free-text query")) -> dict[str, object]:
    """Search across both capability and tool stores by id/name/description/tags."""
    tool_hits = get_tool_store().search(q)
    q_low = (q or "").strip().lower()
    cap_hits = []
    for c in get_store().list("active"):
        hay = " ".join([c.capability_id, c.name, c.description, c.owner]).lower()
        if not q_low or q_low in hay:
            cap_hits.append(c)
    return {
        "query": q,
        "tools": [t.model_dump() for t in tool_hits],
        "capabilities": [c.model_dump() for c in cap_hits],
        "total": len(tool_hits) + len(cap_hits),
    }
