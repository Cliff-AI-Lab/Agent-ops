"""DependencyGraphImpl + builder helpers (V2.4 W1)."""
from __future__ import annotations

from collections import defaultdict, deque
from pathlib import Path
from typing import Iterable

from app.registry.dependency_graph.interface import (
    DependencyEdge,
    ImpactReport,
)


class DependencyGraphImpl:
    """In-memory adjacency lists; forward + reverse maintained together."""

    def __init__(self) -> None:
        self._forward: dict[str, set[str]] = defaultdict(set)
        self._reverse: dict[str, set[str]] = defaultdict(set)
        self._edges: list[DependencyEdge] = []
        self._assets: set[str] = set()

    def add_edge(self, edge: DependencyEdge) -> None:
        if edge.source_id == edge.target_id:
            raise ValueError(f"self-loop disallowed: {edge.source_id}")
        self._forward[edge.source_id].add(edge.target_id)
        self._reverse[edge.target_id].add(edge.source_id)
        self._edges.append(edge)
        self._assets.add(edge.source_id)
        self._assets.add(edge.target_id)

    def add_node(self, asset_id: str) -> None:
        """Register an asset that has no edges yet (leaf or isolated)."""
        self._assets.add(asset_id)

    def depends_on(self, asset_id: str) -> list[str]:
        """Direct outgoing — what this asset references."""
        return sorted(self._forward.get(asset_id, set()))

    def dependents_of(self, asset_id: str) -> list[str]:
        """Direct incoming — what references this asset."""
        return sorted(self._reverse.get(asset_id, set()))

    def impact_of(self, asset_id: str) -> ImpactReport:
        """BFS reverse edges from asset_id; collect all transitive dependents."""
        direct = self.dependents_of(asset_id)
        seen: set[str] = set()
        queue: deque[str] = deque(direct)
        traversed: list[DependencyEdge] = []
        while queue:
            cur = queue.popleft()
            if cur in seen:
                continue
            seen.add(cur)
            for upstream in self.dependents_of(cur):
                if upstream not in seen:
                    queue.append(upstream)
            for e in self._edges:
                if e.target_id == cur or e.source_id == cur:
                    traversed.append(e)
        return ImpactReport(
            root_asset_id=asset_id,
            direct_dependents=direct,
            all_dependents=sorted(seen),
            edges_traversed=traversed,
        )

    def has_cycle(self) -> bool:
        """DFS-based cycle detection."""
        white = set(self._assets)
        gray: set[str] = set()
        black: set[str] = set()

        def dfs(node: str) -> bool:
            if node in black:
                return False
            if node in gray:
                return True  # back edge -> cycle
            gray.add(node)
            for nxt in self._forward.get(node, set()):
                if dfs(nxt):
                    return True
            gray.discard(node)
            black.add(node)
            return False

        for n in list(white):
            if dfs(n):
                return True
        return False

    def all_assets(self) -> list[str]:
        return sorted(self._assets)


def build_graph_from_atoms_and_prompts(
    atoms: dict,    # dict[asset_id, AtomDef]
    prompts: dict,  # dict[asset_id, PromptDef]
) -> DependencyGraphImpl:
    """Walk atoms + prompts to extract V2.4 W1 edges.

    Edges currently:
      - For each atom whose io_schema mentions a prompt_id input field,
        emit edge atom -> prompt (kind='references_prompt').

    V2.4 W2 will scan agent specimens / multi-agent specs.
    """
    graph = DependencyGraphImpl()
    for aid in atoms:
        graph.add_node(aid)
    for pid in prompts:
        graph.add_node(pid)

    for aid, atom in atoms.items():
        # Only LLM-class atoms typically reference prompt_id, but we walk
        # generically for any inputs[].name == 'prompt_id' AND a default value.
        io = getattr(atom, "io_schema", None)
        if not io:
            continue
        inputs = getattr(io, "inputs", None) or {}
        # AtomDef.io_schema.inputs is dict[str, dict] in our schema
        properties = inputs.get("properties") if isinstance(inputs, dict) else None
        if not properties:
            continue
        prompt_field = properties.get("prompt_id")
        if not prompt_field:
            continue
        # Use the field's "default" value if it points to a known prompt
        default = prompt_field.get("default") if isinstance(prompt_field, dict) else None
        if default and default in prompts:
            graph.add_edge(DependencyEdge(
                source_id=aid,
                target_id=default,
                kind="references_prompt",
                note=f"atom {aid!r} default prompt_id -> {default!r}",
            ))
    return graph
