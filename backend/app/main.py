from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from app.api.config import router as config_router
from app.api.deliver import router as deliver_router
from app.api.delivery import router as delivery_router
from app.api.generate import router as generate_router
from app.api.graph import router as graph_router
from app.api.intent import router as intent_router
from app.api.workflows import router as workflows_router
from app.api.marketplace import router as marketplace_router
from app.api.plan import router as plan_router
from app.api.registry import router as registry_router
from app.api.sessions import router as sessions_router
from app.api.trace import router as trace_router
from app.api.triage import router as triage_router
from app.api.wiki import router as wiki_router
from app.config import get_settings

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(
    title="Agent Ops",
    description="Deterministic runtime coordinating heterogeneous agents through typed capability contracts.",
    version="0.5.0",
)
app.include_router(sessions_router)
app.include_router(generate_router)
app.include_router(delivery_router)
app.include_router(config_router)
app.include_router(trace_router)
app.include_router(plan_router)
app.include_router(deliver_router)
app.include_router(registry_router)
app.include_router(wiki_router)
app.include_router(graph_router)
app.include_router(intent_router)
app.include_router(workflows_router)
app.include_router(marketplace_router)
app.include_router(triage_router)


@app.on_event("startup")
async def _bootstrap_registry() -> None:
    """Load every tier of the registry pyramid into in-process stores."""
    from app.registry.agent_store import bootstrap_agents
    from app.registry.bootstrap import bootstrap_registry
    from app.registry.preset_store import bootstrap_presets
    from app.registry.tool_store import bootstrap_tools
    from app.marketplace.asset_store import bootstrap_asset_store
    from app.marketplace.auto_register import reload_generated_agents
    bootstrap_registry()   # capabilities/*.yaml (composite tier · M2)
    bootstrap_tools()      # tools/**/*.yaml     (atom tier · Batch C+)
    bootstrap_presets()    # presets/*/preset.yaml (preset tier · Batch C++)
    bootstrap_agents()     # agents/*/agent.yaml  (agent tier · Batch Y)
    bootstrap_asset_store()  # generated_assets table     (Batch X)
    reload_generated_agents()  # promoted user-generated agents (Batch X)


@app.get("/health")
async def health() -> dict[str, str]:
    """Return service health state."""
    return {"status": "ok", "env": get_settings().harness_env}


if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    async def index() -> FileResponse:
        """Serve the single-file MVP frontend."""
        return FileResponse(STATIC_DIR / "index.html")


def run() -> None:
    """Run the FastAPI application."""
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)
