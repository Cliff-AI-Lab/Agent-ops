"""SearchEngine Protocol."""

from __future__ import annotations

from typing import Protocol

from app.registry.atom_loader import AtomDef


class SearchEngine(Protocol):
    """Embedding-based atom retrieval with hard health filter (v2 audit)."""

    def search_atoms(
        self,
        query: str,
        top_k: int = 5,
        subcategory: str | None = None,
        require_health: str = "green",
    ) -> list[tuple[AtomDef, float]]:
        """Return atoms ranked by relevance with similarity score.

        Hard rule (v2 audit): atoms with health != require_health are filtered out
        BEFORE ranking. Never returned even if relevant.
        """
        ...
