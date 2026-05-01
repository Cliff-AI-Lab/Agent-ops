"""IntentParser Protocol."""

from __future__ import annotations

from typing import Protocol

from app.core.pipelines.factory.ir import StructuredIntent


class IntentParser(Protocol):
    """Parse natural-language requirement into a StructuredIntent."""

    async def parse(self, nl: str) -> StructuredIntent:
        """Parse NL -> StructuredIntent.

        Args:
            nl: Natural-language requirement.

        Returns:
            StructuredIntent with abstract verbs (no atom binding).
        """
        ...
