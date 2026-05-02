"""Phase 7 Industry Router component.

Stage 1 of the two-stage Phase 7 scheduler:
  IndustryRouter (this) -> IndustryClassification -> Industry Designer -> single|multi-agent

Per [[Phase-7-多智能体与行业理解]] § 双阶段调度.
"""
from app.core.pipelines.factory.industry.interface import (
    IndustryClassification,
    IndustryRouter,
)
from app.core.pipelines.factory.industry.impl import (
    IndustryRouterImpl,
    _heuristic_fallback,
)

__all__ = [
    "IndustryClassification",
    "IndustryRouter",
    "IndustryRouterImpl",
    "_heuristic_fallback",
]
