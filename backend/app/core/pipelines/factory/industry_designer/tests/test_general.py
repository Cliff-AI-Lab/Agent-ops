"""Tests for GeneralDesigner - Phase 7 W1 seed."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.llm.client import ChatMessage
from app.core.pipelines.factory.industry.interface import IndustryClassification
from app.core.pipelines.factory.industry_designer import (
    GeneralDesigner,
    _agent_class_for_scenario,
    _default_guardrails,
)


# ---- Helpers -----------------------------------------------------------------


def _make_designer_with_mock(*responses: str) -> GeneralDesigner:
    llm = MagicMock()
    llm.chat = AsyncMock(
        side_effect=[ChatMessage(role="assistant", content=r) for r in responses]
    )
    router = MagicMock()
    router.resolve = MagicMock(return_value="test-model")
    return GeneralDesigner(llm_client=llm, model_router=router)


def _airline_cs_classification() -> IndustryClassification:
    return IndustryClassification(
        industry_code="01",
        primary="通用",
        sub=None,
        business_scenario="客服",
        is_multi_agent=True,
        confidence=0.92,
        reasoning="航司客服需要 triage + 多 specialist 协调（订票/退票/座位/FAQ）",
    )


def _airline_cs_extraction_payload() -> dict:
    return {
        "system_name": "Airline Customer Service System",
        "specialists": [
            {
                "id": "flight_info",
                "name": "Flight Information",
                "description": "查询航班实时状态、中转风险、替代航班建议",
                "nl_brief": "回答航班状态相关问题与重新规划",
                "handoff_targets": ["booking", "faq"],
            },
            {
                "id": "booking",
                "name": "Booking Cancellation",
                "description": "处理订票、改签、退票申请",
                "nl_brief": "订票改签退票相关请求",
                "handoff_targets": ["seat", "refunds"],
            },
            {
                "id": "seat",
                "name": "Seat Special Services",
                "description": "选座、特殊服务申请（前排、医疗）",
                "nl_brief": "处理座位选择与特殊医疗需求",
                "handoff_targets": ["flight_info"],
            },
            {
                "id": "faq",
                "name": "FAQ Agent",
                "description": "回答行李、赔偿、wifi 等政策问题",
                "nl_brief": "回答通用航司政策与常见问题",
                "handoff_targets": ["refunds"],
            },
            {
                "id": "refunds",
                "name": "Refunds Compensation",
                "description": "处理延误赔偿、酒店餐补、退款",
                "nl_brief": "处理延误补偿、酒店餐补、退款申请",
                "handoff_targets": ["faq"],
            },
        ],
        "triage_initial_targets": ["flight_info", "booking", "seat", "faq", "refunds"],
        "shared_context_fields": [
            {
                "name": "confirmation_number",
                "type": "string",
                "description": "passenger confirmation code",
            },
            {
                "name": "flight_number",
                "type": "string",
                "description": "current flight identifier",
            },
        ],
    }


# ---- Helpers tests -----------------------------------------------------------


def test_default_guardrails_contains_relevance_and_jailbreak():
    g = _default_guardrails()
    kinds = {x.kind for x in g}
    assert "relevance" in kinds
    assert "jailbreak" in kinds
    assert all(x.blocking for x in g)


def test_agent_class_for_scenario_routes():
    assert _agent_class_for_scenario("客服") == "customer_service"
    assert _agent_class_for_scenario("数据分析") == "data_analytics"
    assert _agent_class_for_scenario("文档归档") == "doc"
    assert _agent_class_for_scenario("语音客服") == "voice"
    assert _agent_class_for_scenario("研究调研") == "research"
    assert _agent_class_for_scenario("方案提案") == "proposal"
    assert _agent_class_for_scenario("本体建模") == "modeling"
    assert _agent_class_for_scenario(None) == "customer_service"
    assert _agent_class_for_scenario("未知场景") == "customer_service"


# ---- design_multi_agent happy path ------------------------------------------


@pytest.mark.asyncio
async def test_designs_airline_cs_equivalent():
    """cs-agents-demo equivalent: triage + 5 specialists + handoffs + guardrails."""
    payload = _airline_cs_extraction_payload()
    designer = _make_designer_with_mock(json.dumps(payload))

    spec = await designer.design_multi_agent(
        nl="设计一个航司客服系统：包含订票、退票、选座、行李 FAQ、补偿处理多个专员",
        classification=_airline_cs_classification(),
    )

    assert spec.name == "Airline Customer Service System"
    assert spec.industry.code == "01"
    assert spec.industry.business_scenario == "客服"
    assert len(spec.specialists) == 5
    assert {s.id for s in spec.specialists} == {
        "flight_info", "booking", "seat", "faq", "refunds"
    }
    # All specialists got customer_service agent_class (per scenario routing)
    assert all(s.agent_class == "customer_service" for s in spec.specialists)

    # Triage initial targets = all specialists
    assert set(spec.triage.initial_handoff_targets) == {
        "flight_info", "booking", "seat", "faq", "refunds"
    }

    # Handoffs reconstructed from per-specialist handoff_targets
    handoff_pairs = {(h.from_specialist, h.to_specialist) for h in spec.handoffs}
    assert ("flight_info", "booking") in handoff_pairs
    assert ("booking", "refunds") in handoff_pairs
    assert ("faq", "refunds") in handoff_pairs

    # Default guardrails added
    assert {g.kind for g in spec.guardrails} >= {"relevance", "jailbreak"}

    # Shared context
    assert {c.name for c in spec.shared_context} == {"confirmation_number", "flight_number"}

    # Runtime locked per Phase 7 decision
    assert spec.runtime == "openai_agents_sdk"

    # Graph validates clean
    assert spec.validate_graph() == []


@pytest.mark.asyncio
async def test_rejects_single_agent_classification():
    """Designer must refuse to run if classification.is_multi_agent=False."""
    designer = _make_designer_with_mock("{}")  # never reached
    cls = IndustryClassification(
        industry_code="01",
        primary="通用",
        is_multi_agent=False,
        confidence=0.9,
        reasoning="单工作流场景，不需要多 agent 协调",
    )
    with pytest.raises(ValueError, match="is_multi_agent=False"):
        await designer.design_multi_agent("测试需求", cls)


@pytest.mark.asyncio
async def test_rejects_empty_nl():
    designer = _make_designer_with_mock("{}")
    with pytest.raises(ValueError, match="empty"):
        await designer.design_multi_agent("", _airline_cs_classification())


@pytest.mark.asyncio
async def test_recovers_on_first_invalid_then_valid():
    """LLM emits garbage first, valid JSON on retry."""
    designer = _make_designer_with_mock(
        "not json at all",
        json.dumps(_airline_cs_extraction_payload()),
    )
    spec = await designer.design_multi_agent(
        "航司客服多 agent 系统", _airline_cs_classification()
    )
    assert len(spec.specialists) == 5


@pytest.mark.asyncio
async def test_triage_targets_filtered_to_valid_ids():
    """LLM-supplied triage_initial_targets are filtered to existing specialist ids."""
    payload = _airline_cs_extraction_payload()
    payload["triage_initial_targets"].append("ghost_specialist_doesnt_exist")
    designer = _make_designer_with_mock(json.dumps(payload))

    spec = await designer.design_multi_agent(
        "航司客服多 agent 系统", _airline_cs_classification()
    )
    assert "ghost_specialist_doesnt_exist" not in spec.triage.initial_handoff_targets


@pytest.mark.asyncio
async def test_handoff_to_unknown_specialist_filtered():
    """Specialist's handoff_targets pointing to non-existent ids are dropped."""
    payload = _airline_cs_extraction_payload()
    payload["specialists"][0]["handoff_targets"].append("nonexistent")
    designer = _make_designer_with_mock(json.dumps(payload))

    spec = await designer.design_multi_agent(
        "航司客服多 agent 系统", _airline_cs_classification()
    )
    handoff_targets = {h.to_specialist for h in spec.handoffs}
    assert "nonexistent" not in handoff_targets
    # Graph still validates
    assert spec.validate_graph() == []


@pytest.mark.asyncio
async def test_data_analytics_scenario_routes_agent_class():
    """Non-customer-service scenarios pick correct L4 agent_class."""
    payload = {
        "system_name": "Sales BI System",
        "specialists": [
            {
                "id": "trend_agent", "name": "Trend Agent",
                "description": "趋势分析与异常监控数据分析专员",
                "nl_brief": "对销售数据做趋势分析与异常检测",
                "handoff_targets": [],
            },
            {
                "id": "report_agent", "name": "Report Agent",
                "description": "中文 BI 报表生成与发布的专员",
                "nl_brief": "基于分析结果生成中文 BI 报表",
                "handoff_targets": [],
            },
        ],
        "triage_initial_targets": ["trend_agent", "report_agent"],
        "shared_context_fields": [],
    }
    designer = _make_designer_with_mock(json.dumps(payload))
    cls = IndustryClassification(
        industry_code="01",
        primary="通用",
        business_scenario="数据分析",
        is_multi_agent=True,
        confidence=0.9,
        reasoning="多 agent 协作的数据分析场景，需要趋势 + 报表两个专员",
    )
    spec = await designer.design_multi_agent("销售 BI 多 agent 系统", cls)
    assert all(s.agent_class == "data_analytics" for s in spec.specialists)


@pytest.mark.asyncio
async def test_at_least_2_specialists_required():
    """Designer rejects extraction with < 2 specialists (multi-agent needs 2+)."""
    payload = {
        "system_name": "Solo",
        "specialists": [
            {
                "id": "lonely",
                "name": "Lonely",
                "description": "single specialist invalid for multi-agent",
                "nl_brief": "alone here",
                "handoff_targets": [],
            }
        ],
        "triage_initial_targets": ["lonely"],
        "shared_context_fields": [],
    }
    designer = _make_designer_with_mock(
        json.dumps(payload),
        json.dumps(payload),  # retry also returns invalid
    )
    with pytest.raises(ValueError, match=">= 2"):
        await designer.design_multi_agent(
            "测试单 specialist 场景", _airline_cs_classification()
        )


def test_industry_code_attribute():
    """Designer declares which industry it serves (per registry registration)."""
    assert GeneralDesigner.industry_code == "01"
