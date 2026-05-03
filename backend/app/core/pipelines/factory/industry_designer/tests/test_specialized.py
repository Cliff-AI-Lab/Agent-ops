"""Tests for industry-specialized Designers (V2.1.0 W3)."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.llm.client import ChatMessage
from app.core.pipelines.factory.industry.interface import IndustryClassification
from app.core.pipelines.factory.industry_designer import (
    FinanceDesigner,
    GovernmentDesigner,
    MedicalDesigner,
    RetailDesigner,
    default_registry,
)


def _classification(industry_code: str, scenario: str = "客服") -> IndustryClassification:
    return IndustryClassification(
        industry_code=industry_code,
        primary={"02": "金融", "06": "医疗", "08": "政务", "09": "零售"}.get(industry_code, "通用"),
        sub=None,
        business_scenario=scenario,
        is_multi_agent=True,
        confidence=0.92,
        reasoning="测试 reasoning 字段需要 10 字符以上以满足验证",
    )


def _extraction_payload() -> dict:
    return {
        "system_name": "Test System",
        "specialists": [
            {
                "id": "a", "name": "A",
                "description": "处理 A 类业务请求的专员描述",
                "nl_brief": "处理 A 类业务请求并响应用户",
                "handoff_targets": ["b"],
            },
            {
                "id": "b", "name": "B",
                "description": "处理 B 类业务请求的专员描述",
                "nl_brief": "处理 B 类业务请求并响应用户",
                "handoff_targets": ["a"],
            },
        ],
        "triage_initial_targets": ["a", "b"],
        "shared_context_fields": [],
    }


def _make_mock(designer_class):
    llm = MagicMock()
    llm.chat = AsyncMock(
        return_value=ChatMessage(role="assistant", content=json.dumps(_extraction_payload()))
    )
    router = MagicMock()
    router.resolve = MagicMock(return_value="test-model")
    return designer_class(llm_client=llm, model_router=router)


# ---- Industry codes -------------------------------------------------------


def test_industry_codes_aligned():
    """Each Designer's class attribute matches taxonomy."""
    assert FinanceDesigner.industry_code == "02"
    assert MedicalDesigner.industry_code == "06"
    assert GovernmentDesigner.industry_code == "08"
    assert RetailDesigner.industry_code == "09"


# ---- Finance --------------------------------------------------------------


@pytest.mark.asyncio
async def test_finance_injects_compliance_pii_guardrails():
    designer = _make_mock(FinanceDesigner)
    spec = await designer.design_multi_agent(
        "做一个银行客服系统包含 A B 两个专员", _classification("02")
    )
    kinds = {g.kind for g in spec.guardrails}
    # GeneralDesigner injects relevance + jailbreak; FinanceDesigner adds compliance + pii
    assert {"relevance", "jailbreak", "compliance", "pii"}.issubset(kinds)


@pytest.mark.asyncio
async def test_finance_injects_audit_trail_context():
    designer = _make_mock(FinanceDesigner)
    spec = await designer.design_multi_agent(
        "做一个银行客服系统包含 A B 两个专员", _classification("02")
    )
    names = {c.name for c in spec.shared_context}
    assert {"customer_id", "account_id", "audit_trail_id"}.issubset(names)


# ---- Medical --------------------------------------------------------------


@pytest.mark.asyncio
async def test_medical_injects_pii_compliance_guardrails():
    designer = _make_mock(MedicalDesigner)
    spec = await designer.design_multi_agent(
        "做一个医院导诊系统包含 A B 两个专员", _classification("06", "导诊")
    )
    kinds = {g.kind for g in spec.guardrails}
    assert {"relevance", "jailbreak", "pii", "compliance"}.issubset(kinds)
    # Compliance should mention diagnosis
    compliance = next(g for g in spec.guardrails if g.kind == "compliance")
    assert "diagnosis" in compliance.description.lower() or "诊断" in compliance.description


@pytest.mark.asyncio
async def test_medical_injects_patient_context():
    designer = _make_mock(MedicalDesigner)
    spec = await designer.design_multi_agent(
        "做一个医院导诊系统包含 A B 两个专员", _classification("06", "导诊")
    )
    names = {c.name for c in spec.shared_context}
    assert {"patient_id", "encounter_id"}.issubset(names)


# ---- Government -----------------------------------------------------------


@pytest.mark.asyncio
async def test_government_injects_citizen_id_context():
    designer = _make_mock(GovernmentDesigner)
    spec = await designer.design_multi_agent(
        "做一个政务热线包含 A B 两个专员", _classification("08", "政务热线")
    )
    names = {c.name for c in spec.shared_context}
    assert {"citizen_id", "case_number", "responsible_department"}.issubset(names)


# ---- Retail ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_retail_injects_order_cart_context():
    designer = _make_mock(RetailDesigner)
    spec = await designer.design_multi_agent(
        "做一个电商客服系统包含 A B 两个专员", _classification("09", "电商客服")
    )
    names = {c.name for c in spec.shared_context}
    assert {"customer_id", "order_id", "cart_id"}.issubset(names)


# ---- No duplicate guardrails ---------------------------------------------


@pytest.mark.asyncio
async def test_no_duplicate_guardrails_when_extras_overlap():
    """If General already injected 'pii', specialized must not re-add."""
    designer = _make_mock(FinanceDesigner)
    spec = await designer.design_multi_agent(
        "做一个银行客服系统包含 A B 两个专员", _classification("02")
    )
    kinds = [g.kind for g in spec.guardrails]
    assert kinds.count("pii") == 1  # not duplicated even if both layers say pii


# ---- Default registry includes all -------------------------------------


def test_default_registry_includes_all_v210_w3_designers():
    reg = default_registry()
    assert reg.get("01") is not None  # General
    assert isinstance(reg.get("02"), FinanceDesigner)
    assert isinstance(reg.get("06"), MedicalDesigner)
    assert isinstance(reg.get("08"), GovernmentDesigner)
    assert isinstance(reg.get("09"), RetailDesigner)


def test_default_registry_all_12_industries_resolve():
    """V2.1.0 W3 ships all 11 specialized + General; every code 01..12 returns
    a non-None Designer (specialized for shipped, General for any future gap)."""
    from app.core.pipelines.factory.industry_designer import (
        EducationDesigner,
        EnergyDesigner,
        ManufacturingDesigner,
        MediaDesigner,
        SmartCityDesigner,
        TelecomDesigner,
        TransportationDesigner,
    )

    reg = default_registry()
    expected_specialized = {
        "02": FinanceDesigner,
        "03": ManufacturingDesigner,
        "04": EnergyDesigner,
        "05": TransportationDesigner,
        "06": MedicalDesigner,
        "07": EducationDesigner,
        "08": GovernmentDesigner,
        "09": RetailDesigner,
        "10": MediaDesigner,
        "11": TelecomDesigner,
        "12": SmartCityDesigner,
    }
    for code, expected_cls in expected_specialized.items():
        designer = reg.get(code)
        assert isinstance(designer, expected_cls), (
            f"industry {code} expected {expected_cls.__name__} got {type(designer).__name__}"
        )
    # 01 General
    from app.core.pipelines.factory.industry_designer import GeneralDesigner
    assert type(reg.get("01")).__name__ == "GeneralDesigner"


@pytest.mark.asyncio
async def test_telecom_double_guardrails():
    """Telecom has 2 industry-specific guardrails (pii + compliance)."""
    from app.core.pipelines.factory.industry_designer import TelecomDesigner
    designer = _make_mock(TelecomDesigner)
    spec = await designer.design_multi_agent(
        "做一个运营商客服系统包含 A B 两个专员", _classification("11", "运营商客服")
    )
    kinds = [g.kind for g in spec.guardrails]
    # Two of each are normal: relevance + jailbreak (general) + pii + compliance (telecom)
    assert kinds.count("pii") == 1
    assert kinds.count("compliance") == 1
    names = {c.name for c in spec.shared_context}
    assert {"subscriber_id", "msisdn", "service_id", "ticket_id"}.issubset(names)


@pytest.mark.asyncio
async def test_manufacturing_safety_compliance():
    from app.core.pipelines.factory.industry_designer import ManufacturingDesigner
    designer = _make_mock(ManufacturingDesigner)
    spec = await designer.design_multi_agent(
        "做一个工厂运维多专员系统 A B", _classification("03", "工厂运维")
    )
    compliance = [g for g in spec.guardrails if g.kind == "compliance"]
    assert len(compliance) >= 1
    assert "safety" in compliance[0].description.lower() or "safety-critical" in compliance[0].description
    names = {c.name for c in spec.shared_context}
    assert {"work_order_id", "equipment_id", "shift"}.issubset(names)
