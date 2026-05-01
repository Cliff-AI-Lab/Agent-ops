"""SearchEngine V1: keyword + tag-overlap scoring with hard filters.

V1 (Phase 1 W1): keyword + tag overlap. No external LLM/embedding deps.
V2 (Phase 2): replace _score with embedding cosine. Interface stays same.

Hard filters (v2 audit):
  - health != require_health -> filtered BEFORE ranking
  - subcategory mismatch -> filtered BEFORE ranking
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from app.registry.atom_loader import AtomDef, AtomLoaderImpl
from app.core.trace.bus import emit


_TOKEN_RE = re.compile(r"[一-鿿]+|[a-zA-Z][a-zA-Z0-9_]+")


def _tokenize(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(text)}


class SearchEngineImpl:
    """V1 lexical search. Phase 2 swap to embeddings, same interface."""

    def __init__(
        self,
        atom_loader: AtomLoaderImpl | None = None,
        atoms: dict[str, AtomDef] | None = None,
    ) -> None:
        self._loader = atom_loader
        self._atoms: dict[str, AtomDef] = atoms or {}

    def index(self, atoms: Iterable[AtomDef]) -> None:
        """Replace the in-memory atom corpus."""
        self._atoms = {a.asset_id: a for a in atoms}
        emit("L3", "SearchEngine", "indexed", f"n={len(self._atoms)}")

    def search_atoms(
        self,
        query: str,
        top_k: int = 5,
        subcategory: str | None = None,
        require_health: str = "green",
    ) -> list[tuple[AtomDef, float]]:
        if not query.strip():
            return []
        if not self._atoms:
            emit("L3", "SearchEngine", "empty_corpus", "no atoms indexed")
            return []

        q_tokens = _tokenize(query)

        candidates: list[tuple[AtomDef, float]] = []
        for atom in self._atoms.values():
            if not self._passes_hard_filters(atom, subcategory, require_health):
                continue
            score = self._score(atom, query, q_tokens)
            if score > 0:
                candidates.append((atom, score))

        candidates.sort(key=lambda x: x[1], reverse=True)
        result = candidates[:top_k]
        emit(
            "L3",
            "SearchEngine",
            "search_done",
            f"q={query[:30]!r} sub={subcategory} -> {len(result)}/{len(self._atoms)}",
        )
        return result

    @staticmethod
    def _passes_hard_filters(
        atom: AtomDef,
        subcategory: str | None,
        require_health: str,
    ) -> bool:
        if subcategory and atom.subcategory != subcategory:
            return False
        if atom.health_check is not None:
            if atom.health_check.status != require_health:
                return False
        return True

    @staticmethod
    def _score(atom: AtomDef, query: str, q_tokens: set[str]) -> float:
        """V1 lexical score: tag overlap + name match + description keyword density.

        Returns 0..1 normalized score.
        """
        tag_tokens = {t.lower() for t in atom.tags}
        tag_overlap = len(q_tokens & tag_tokens) / max(len(q_tokens), 1)

        name_tokens = _tokenize(f"{atom.name} {atom.name_en or ''}")
        name_overlap = len(q_tokens & name_tokens) / max(len(q_tokens), 1)

        desc_tokens = _tokenize(atom.description)
        desc_overlap = len(q_tokens & desc_tokens) / max(len(q_tokens), 1)

        recommend_boost = 0.1 if atom.recommend else 0.0

        return (
            0.5 * tag_overlap
            + 0.3 * name_overlap
            + 0.15 * desc_overlap
            + recommend_boost
        )
