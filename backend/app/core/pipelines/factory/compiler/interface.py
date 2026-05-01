"""DSLCompiler Protocol."""

from __future__ import annotations

from typing import Protocol

from app.core.pipelines.factory.ir import ResolvedDAG


class DSLCompiler(Protocol):
    """Compile a ResolvedDAG to a target platform DSL.

    PURE TEMPLATE-BASED: NO LLM in this stage. Determinism is guaranteed.
    """

    def compile(self, dag: ResolvedDAG) -> str:
        """Compile -> DSL string (Dify YAML / n8n JSON).

        Args:
            dag: ResolvedDAG with target == this compiler's target.

        Returns:
            DSL string ready for deployment.
        """
        ...
