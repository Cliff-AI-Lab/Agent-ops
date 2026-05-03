"""DesignerRegistry - dispatch industry_code -> IndustryDesigner.

Per [[Phase-7-多智能体与行业理解]]: V2.1.0 W1 ships GeneralDesigner only;
V2.1.1+ batch onboards 11 industry-specific Designers. The registry pattern
lets new Designers register themselves without touching consumer code.

Lookup order:
  1. exact match on industry_code
  2. fallback to '01' (通用 = GeneralDesigner) if exact missing
  3. raise KeyError if even '01' missing (misconfiguration)
"""
from __future__ import annotations

import logging
from typing import Optional

from app.core.pipelines.factory.industry_designer.general import GeneralDesigner
from app.core.pipelines.factory.industry_designer.interface import IndustryDesigner

logger = logging.getLogger(__name__)


class DesignerRegistry:
    """Industry-code -> Designer instance map with fallback to General."""

    def __init__(self, default_industry_code: str = "01") -> None:
        self._default = default_industry_code
        self._designers: dict[str, IndustryDesigner] = {}

    def register(self, designer: IndustryDesigner) -> None:
        """Register a designer; replaces any existing registration for that code."""
        code = designer.industry_code
        if code in self._designers:
            logger.warning(
                "DesignerRegistry replacing existing designer for %s: %s -> %s",
                code,
                type(self._designers[code]).__name__,
                type(designer).__name__,
            )
        self._designers[code] = designer

    def get(self, industry_code: str) -> IndustryDesigner:
        """Resolve designer by code; falls back to default (01 General) if missing."""
        if industry_code in self._designers:
            return self._designers[industry_code]
        if self._default in self._designers:
            logger.info(
                "no designer for industry %s; falling back to default %s",
                industry_code, self._default,
            )
            return self._designers[self._default]
        raise KeyError(
            f"no designer registered for industry_code {industry_code!r} "
            f"AND default {self._default!r} not registered. "
            f"Registered: {list(self._designers)}"
        )

    def codes(self) -> list[str]:
        return sorted(self._designers.keys())

    def __contains__(self, industry_code: str) -> bool:
        return industry_code in self._designers


def default_registry(
    llm_client=None,
    model_router=None,
) -> DesignerRegistry:
    """Build the V2.1.0 default registry with all currently shipped Designers.

    V2.1.0 W1+W2: GeneralDesigner only (industry_code='01').
    V2.1.0 W3 (this version): + FinanceDesigner / MedicalDesigner /
                                GovernmentDesigner / RetailDesigner.
    V2.1.0 W4+: + Manufacturing / Energy / Transportation / Education /
                  Media / Telecom / SmartCity (TBD).
    """
    # Imports here to avoid circular reference at module load time
    from app.core.pipelines.factory.industry_designer.specialized import (
        EducationDesigner,
        EnergyDesigner,
        FinanceDesigner,
        GovernmentDesigner,
        ManufacturingDesigner,
        MediaDesigner,
        MedicalDesigner,
        RetailDesigner,
        SmartCityDesigner,
        TelecomDesigner,
        TransportationDesigner,
    )

    reg = DesignerRegistry(default_industry_code="01")
    reg.register(GeneralDesigner(llm_client=llm_client, model_router=model_router))
    for cls in (
        FinanceDesigner,
        ManufacturingDesigner,
        EnergyDesigner,
        TransportationDesigner,
        MedicalDesigner,
        EducationDesigner,
        GovernmentDesigner,
        RetailDesigner,
        MediaDesigner,
        TelecomDesigner,
        SmartCityDesigner,
    ):
        reg.register(cls(llm_client=llm_client, model_router=model_router))
    return reg
