"""IntentParserImpl - LLM-backed implementation.

Phase 1 W2 day 1-2: Implement parse() using Ruidong gateway + JSON Schema strict.
Until then: stub raises NotImplementedError.
"""

from __future__ import annotations

from app.core.pipelines.factory.ir import StructuredIntent


class IntentParserImpl:
    """LLM-backed IntentParser. Calls iruidong.com/v1/chat/completions."""

    def __init__(self, llm_client=None) -> None:
        self._llm = llm_client

    async def parse(self, nl: str) -> StructuredIntent:
        raise NotImplementedError(
            "IntentParserImpl.parse() will be implemented in Phase 1 W2. "
            "See: E:/Obsidian/Agent 工厂/工程规范/编译器IR契约.md § 二"
        )
