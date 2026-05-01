"""Recall submodule: candidate atom retrieval via SearchEngine.

Pure wrapper around SearchEngine. No LLM calls here.
"""

from __future__ import annotations

from app.core.pipelines.factory.ir import StepSpec
from app.registry.atom_loader import AtomDef
from app.registry.search_engine import SearchEngineImpl


def recall_for_step(
    step: StepSpec,
    search_engine: SearchEngineImpl,
    top_k: int = 5,
) -> list[tuple[AtomDef, float]]:
    """Find candidate atoms for an abstract step.

    Uses verb + constraint values as query. Subcategory hint applied as hard filter.
    health != green filtered by SearchEngine itself.
    """
    parts: list[str] = [step.verb]
    for v in step.constraints.values():
        if isinstance(v, str):
            parts.append(v)
    query = " ".join(parts)
    return search_engine.search_atoms(
        query=query,
        top_k=top_k,
        subcategory=step.suggested_subcategory,
        require_health="green",
    )
