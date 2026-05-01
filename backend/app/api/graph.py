"""Code-graph API endpoints (Batch C+++).

POST /api/graph/parse — parse a CodeArtifact into a Cytoscape-friendly
graph of file/component/external nodes with imports/contains edges.
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from app.core.code_graph import parse_artifact
from app.core.trace.bus import emit
from app.ontology import CodeArtifact
from app.ontology.objects.code_graph import CodeGraph

router = APIRouter(prefix="/api/graph", tags=["graph"])


class ParseRequest(BaseModel):
    """Request body for /api/graph/parse."""

    artifact: CodeArtifact

    model_config = ConfigDict(extra="forbid")


@router.post("/parse")
async def parse(req: ParseRequest) -> CodeGraph:
    """Build a CodeGraph from a CodeArtifact (no LLM, pure regex parser)."""
    graph = parse_artifact(req.artifact)
    emit(
        "L6",
        "CodeGraph",
        "parsed",
        f"nodes={len(graph.nodes)} edges={len(graph.edges)}",
        data={"stats": graph.stats},
    )
    return graph
