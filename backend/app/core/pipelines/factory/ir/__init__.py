"""Intermediate Representations for the factory compiler.

Two IRs:
- StructuredIntent: produced by IntentParser, no atom binding.
- ResolvedDAG: produced by Resolver, atoms bound, type-checked edges.

See: E:/Obsidian/Agent 工厂/工程规范/编译器IR契约.md
"""

from app.core.pipelines.factory.ir.intent import (
    Constraints,
    OutputSpec,
    StepSpec,
    StructuredIntent,
    TriggerSpec,
)
from app.core.pipelines.factory.ir.resolved_dag import (
    ResolvedDAG,
    ResolvedEdge,
    ResolvedNode,
    TypeCheck,
)

__all__ = [
    "Constraints",
    "OutputSpec",
    "ResolvedDAG",
    "ResolvedEdge",
    "ResolvedNode",
    "StepSpec",
    "StructuredIntent",
    "TriggerSpec",
    "TypeCheck",
]
