"""Asset Dependency Graph (V2.4 W1).

Builds a forward + reverse adjacency map across all loaded assets so callers
can answer:
  - which prompts/atoms does asset X reference?  (forward / depends_on)
  - if I upgrade asset X, what will break?       (reverse / impacted_by)
  - is the graph cycle-free?                      (validate)

Edge sources V2.4 W1:
  AtomDef.io_schema may reference prompt_id (LLM atoms)
    -> edge: atom.<...> --references_prompt--> prompt.<...>
  PromptDef has no outbound (leaf)

V2.4 W2 will add:
  L3 combo / L4 specimen edges (when agent specimens land in registry)
  Industry Designer -> Composer -> spec edges
"""
from __future__ import annotations

from typing import Iterable, Protocol

from pydantic import BaseModel, Field


class DependencyEdge(BaseModel):
    """One directed edge in the dep graph."""

    source_id: str  # asset_id of dependent
    target_id: str  # asset_id of dependency
    kind: str       # 'references_prompt' / 'uses_atom' / 'derives_from' / ...
    note: str = ""


class ImpactReport(BaseModel):
    """Result of impact_of(asset_id): all assets transitively depending on it."""

    root_asset_id: str
    direct_dependents: list[str] = Field(default_factory=list)
    all_dependents: list[str] = Field(
        default_factory=list,
        description="Transitive closure of reverse edges (BFS)",
    )
    edges_traversed: list[DependencyEdge] = Field(default_factory=list)


class DependencyGraph(Protocol):
    """Forward + reverse adjacency map across assets."""

    def add_edge(self, edge: DependencyEdge) -> None: ...
    def depends_on(self, asset_id: str) -> list[str]: ...
    def dependents_of(self, asset_id: str) -> list[str]: ...
    def impact_of(self, asset_id: str) -> ImpactReport: ...
    def has_cycle(self) -> bool: ...
    def all_assets(self) -> list[str]: ...
