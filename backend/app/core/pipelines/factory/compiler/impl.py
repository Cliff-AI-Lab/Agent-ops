"""Dify Compiler implementation.

Phase 1 W3: Jinja2-based template rendering. Reads atom.projections.dify per node.
"""

from __future__ import annotations

from app.core.pipelines.factory.ir import ResolvedDAG


class DifyCompilerImpl:
    def __init__(self, atom_loader=None) -> None:
        self._atoms = atom_loader

    def compile(self, dag: ResolvedDAG) -> str:
        if dag.target not in ("dify", "hybrid"):
            raise ValueError(
                f"DifyCompilerImpl only handles dify/hybrid targets, got {dag.target}"
            )
        raise NotImplementedError(
            "DifyCompilerImpl.compile() will be implemented in Phase 1 W3 day 1."
        )
