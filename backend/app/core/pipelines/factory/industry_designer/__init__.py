"""Phase 7 Industry Designer component (stage 2 of two-stage scheduler).

Per [[Phase-7-多智能体与行业理解]] § 双阶段调度.
"""
from app.core.pipelines.factory.industry_designer.interface import IndustryDesigner
from app.core.pipelines.factory.industry_designer.general import (
    GeneralDesigner,
    _agent_class_for_scenario,
    _default_guardrails,
)
from app.core.pipelines.factory.industry_designer.registry import (
    DesignerRegistry,
    default_registry,
)
from app.core.pipelines.factory.industry_designer.specialized import (
    FinanceDesigner,
    GovernmentDesigner,
    MedicalDesigner,
    RetailDesigner,
    _IndustrySpecializedDesigner,
)

__all__ = [
    "IndustryDesigner",
    "GeneralDesigner",
    "DesignerRegistry",
    "default_registry",
    "FinanceDesigner",
    "MedicalDesigner",
    "GovernmentDesigner",
    "RetailDesigner",
    "_IndustrySpecializedDesigner",
    "_agent_class_for_scenario",
    "_default_guardrails",
]
