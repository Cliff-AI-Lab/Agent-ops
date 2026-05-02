"""Tests for IndustryRouterImpl - Phase 7 W1."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from app.core.llm.client import ChatMessage
from app.core.pipelines.factory.industry import (
    IndustryClassification,
    IndustryRouterImpl,
    _heuristic_fallback,
)
from app.core.pipelines.factory.industry.impl import _strip_code_fence


# ---- IndustryClassification model -------------------------------------------


def test_classification_constructs():
    c = IndustryClassification(
        industry_code="01",
        primary="通用",
        is_multi_agent=False,
        confidence=0.9,
        reasoning="测试中文 reasoning 字段长度",
    )
    assert c.industry_code == "01"
    assert c.is_multi_agent is False


def test_classification_rejects_bad_code():
    with pytest.raises(ValidationError, match="industry_code"):
        IndustryClassification(
            industry_code="99",
            primary="通用",
            is_multi_agent=False,
            confidence=0.5,
            reasoning="测试错误代码",
        )


def test_classification_confidence_clamped():
    with pytest.raises(ValidationError):
        IndustryClassification(
            industry_code="01",
            primary="通用",
            is_multi_agent=False,
            confidence=1.5,  # > 1.0
            reasoning="测试越界",
        )


# ---- _strip_code_fence -------------------------------------------------------


def test_strip_code_fence_with_markdown():
    raw = '```json\n{"a": 1}\n```'
    assert _strip_code_fence(raw) == '{"a": 1}'


def test_strip_code_fence_no_fence():
    raw = '{"a": 1}'
    assert _strip_code_fence(raw) == '{"a": 1}'


# ---- _heuristic_fallback -----------------------------------------------------


def test_heuristic_fallback_default_single_agent():
    c = _heuristic_fallback("查销售库写日报推钉钉")
    assert c.industry_code == "01"
    assert c.primary == "通用"
    assert c.is_multi_agent is False
    assert c.confidence < 0.5


def test_heuristic_fallback_detects_multi_agent_keyword():
    c = _heuristic_fallback("做一个多智能体客服系统，包含 triage 和多个 agent")
    assert c.is_multi_agent is True


# ---- IndustryRouterImpl with mocked LLM -------------------------------------


def _make_router_with_mock(llm_response_content: str) -> IndustryRouterImpl:
    llm = MagicMock()
    llm.chat = AsyncMock(
        return_value=ChatMessage(role="assistant", content=llm_response_content)
    )
    router = MagicMock()
    router.resolve = MagicMock(return_value="test-model")
    return IndustryRouterImpl(llm_client=llm, model_router=router)


@pytest.mark.asyncio
async def test_classify_single_agent_path():
    impl = _make_router_with_mock(
        json.dumps(
            {
                "industry_code": "01",
                "primary": "通用",
                "sub": None,
                "business_scenario": "数据日报",
                "is_multi_agent": False,
                "confidence": 0.92,
                "reasoning": "明显是 ETL 单工作流场景，无需多 agent 协调",
            }
        )
    )
    result = await impl.classify("每周一查销售库写中文周报推钉钉")
    assert result.industry_code == "01"
    assert result.is_multi_agent is False
    assert result.confidence == 0.92


@pytest.mark.asyncio
async def test_classify_multi_agent_path():
    impl = _make_router_with_mock(
        json.dumps(
            {
                "industry_code": "01",
                "primary": "通用",
                "sub": None,
                "business_scenario": "客服",
                "is_multi_agent": True,
                "confidence": 0.88,
                "reasoning": "客服场景需要 triage + 多 specialist 协调",
            }
        )
    )
    result = await impl.classify("做一个航司客服系统，包含订票、退票、座位、FAQ 多个 agent")
    assert result.is_multi_agent is True
    assert result.business_scenario == "客服"


@pytest.mark.asyncio
async def test_classify_industry_code_finance():
    impl = _make_router_with_mock(
        json.dumps(
            {
                "industry_code": "02",
                "primary": "金融",
                "sub": "银行",
                "business_scenario": "风控告警",
                "is_multi_agent": False,
                "confidence": 0.95,
                "reasoning": "明确是银行风控告警场景，LLM 评估风险等级",
            }
        )
    )
    result = await impl.classify("每小时查交易库找异常订单 LLM 评估风险等级超 3 级推钉钉")
    assert result.industry_code == "02"
    assert result.primary == "金融"
    assert result.sub == "银行"


@pytest.mark.asyncio
async def test_classify_strips_markdown_fence():
    impl = _make_router_with_mock(
        '```json\n'
        + json.dumps(
            {
                "industry_code": "07",
                "primary": "教育",
                "sub": None,
                "business_scenario": "学情报告",
                "is_multi_agent": False,
                "confidence": 0.80,
                "reasoning": "教育行业学情数据统计场景，单工作流即可",
            }
        )
        + "\n```"
    )
    result = await impl.classify("每周拉教学库本周作业完成数据生成学情简报")
    assert result.industry_code == "07"


@pytest.mark.asyncio
async def test_classify_empty_nl_rejected():
    impl = _make_router_with_mock("{}")
    with pytest.raises(ValueError, match="empty"):
        await impl.classify("")


@pytest.mark.asyncio
async def test_classify_falls_back_after_two_invalid_responses():
    """LLM returns garbage twice -> heuristic fallback (no exception raised)."""
    llm = MagicMock()
    llm.chat = AsyncMock(
        side_effect=[
            ChatMessage(role="assistant", content="not json"),
            ChatMessage(role="assistant", content="still not json"),
        ]
    )
    router = MagicMock()
    router.resolve = MagicMock(return_value="test-model")
    impl = IndustryRouterImpl(llm_client=llm, model_router=router)

    result = await impl.classify("查销售库写日报推钉钉")
    # Heuristic fallback fired
    assert result.industry_code == "01"
    assert result.confidence < 0.5
    assert "兜底" in result.reasoning


@pytest.mark.asyncio
async def test_classify_recovers_on_retry():
    """First LLM response invalid; second valid -> no fallback, parse succeeds."""
    llm = MagicMock()
    llm.chat = AsyncMock(
        side_effect=[
            ChatMessage(role="assistant", content="not json"),
            ChatMessage(
                role="assistant",
                content=json.dumps(
                    {
                        "industry_code": "06",
                        "primary": "医疗",
                        "sub": None,
                        "business_scenario": "门诊预约",
                        "is_multi_agent": False,
                        "confidence": 0.78,
                        "reasoning": "门诊预约自动化场景，简单的 ETL + 通知即可",
                    }
                ),
            ),
        ]
    )
    router = MagicMock()
    router.resolve = MagicMock(return_value="test-model")
    impl = IndustryRouterImpl(llm_client=llm, model_router=router)

    result = await impl.classify("帮医院做一个自动门诊预约系统")
    assert result.industry_code == "06"
    assert result.primary == "医疗"
    assert result.confidence == 0.78  # NOT the heuristic 0.30
