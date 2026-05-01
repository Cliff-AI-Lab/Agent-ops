"""CodeGraph — knowledge graph of a generated CodeArtifact (Batch C+++).

Inspired by GitNexus (https://github.com/abhigyanpatwari/GitNexus). Captures
inter-file relationships (imports), component declarations, and external
package usage so the front end can render an interactive Cytoscape graph
showing how generated files relate to each other.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

NodeType = Literal["file", "directory", "component", "function", "external"]
EdgeType = Literal["imports", "exports", "extends", "calls", "contains"]


class GraphNode(BaseModel):
    """One node in the code graph (a file, component, function, or external pkg)."""

    id: str = Field(..., description="Stable node id (e.g. file path or 'ext:react').")
    label: str = Field(..., description="Display label.")
    type: NodeType
    file_path: str | None = Field(default=None, description="Source file path if applicable.")
    metadata: dict[str, object] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class GraphEdge(BaseModel):
    """A directed relationship between two GraphNodes."""

    id: str
    source: str
    target: str
    type: EdgeType
    metadata: dict[str, object] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class CodeGraph(BaseModel):
    """Aggregate knowledge graph for a CodeArtifact."""

    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    stats: dict[str, int] = Field(
        default_factory=dict,
        description="Counts by node type / edge type for quick UI summary.",
    )

    model_config = ConfigDict(extra="forbid")
