"""Search Engine implementation.

Phase 1 W1 day 5: Embedding-based search. Stub for now.
"""

from __future__ import annotations


class SearchEngineImpl:
    def __init__(self, atom_loader=None, embedder=None) -> None:
        self._atoms = atom_loader
        self._embedder = embedder

    def search_atoms(
        self,
        query: str,
        top_k: int = 5,
        subcategory: str | None = None,
        require_health: str = "green",
    ) -> list:
        raise NotImplementedError(
            "SearchEngineImpl.search_atoms() will be implemented in Phase 1 W1 day 5."
        )
