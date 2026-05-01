"""Factory Pipeline (V2.0.0).

NL -> Intent -> ResolvedDAG -> DSL -> Validated artifact.
See: E:/Obsidian/Agent 工厂/工程规范/编译器IR契约.md
"""

from app.core.pipelines.factory.ir import (
    StructuredIntent,
    TriggerSpec,
    StepSpec,
    OutputSpec,
    Constraints,
    ResolvedDAG,
    ResolvedNode,
    ResolvedEdge,
)

__all__ = [
    "StructuredIntent",
    "TriggerSpec",
    "StepSpec",
    "OutputSpec",
    "Constraints",
    "ResolvedDAG",
    "ResolvedNode",
    "ResolvedEdge",
]
