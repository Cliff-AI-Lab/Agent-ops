"""Resolver Protocol."""

from __future__ import annotations

from typing import Protocol

from app.core.pipelines.factory.ir import ResolvedDAG, StructuredIntent


class Resolver(Protocol):
    """Resolve StructuredIntent -> ResolvedDAG with atoms bound."""

    async def resolve(self, intent: StructuredIntent) -> ResolvedDAG:
        """Bind atoms to abstract steps + check edge types + insert adapters.

        Args:
            intent: Output of IntentParser.

        Returns:
            ResolvedDAG with each node bound to a specific atom.
        """
        ...
