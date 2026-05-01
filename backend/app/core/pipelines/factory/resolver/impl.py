"""ResolverImpl - composes recall, rank, bind submodules.

Phase 1 W2 day 3-5: Real implementation.
Until then: stub raises NotImplementedError.
"""

from __future__ import annotations

from app.core.pipelines.factory.ir import ResolvedDAG, StructuredIntent


class ResolverImpl:
    def __init__(self, search_engine=None, llm_client=None) -> None:
        self._search = search_engine
        self._llm = llm_client

    async def resolve(self, intent: StructuredIntent) -> ResolvedDAG:
        raise NotImplementedError(
            "ResolverImpl.resolve() will be implemented in Phase 1 W2. "
            "See: E:/Obsidian/Agent 工厂/工程规范/编译器IR契约.md § 三"
        )
