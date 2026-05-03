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


def test_default_registry_falls_back_to_general_for_unshipped_industry():
    """E.g., 03 制造 not yet shipped -> falls back to GeneralDesigner."""
    from app.core.pipelines.factory.industry_designer import GeneralDesigner

    reg = default_registry()
    designer = reg.get("03")
    assert isinstance(designer, GeneralDesigner)
    # Make sure it's NOT one of the specialized (i.e. not Finance/Medical/etc.)
    assert not isinstance(designer, FinanceDesigner)
