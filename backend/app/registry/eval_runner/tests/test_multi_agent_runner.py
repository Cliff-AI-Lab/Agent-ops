"""Tests for MultiAgentEvalRunnerImpl."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.llm.client import ChatMessage
from app.core.pipelines.factory.multi_agent_pipeline import MultiAgentFactoryPipeline
from app.registry.eval_runner import (
    EvalCase,
    EvalSet,
    ExpectedShape,
    MultiAgentEvalRunnerImpl,
)


def _classification_payload(industry="01", scenario="客服", is_multi=True) -> dict:
    return {
        "industry_code": industry,
        "primary": "通用" if industry == "01" else "金融",
        "sub": None,
        "business_scenario": scenario,
        "is_multi_agent": is_multi,
        "confidence": 0.92,
        "reasoning": "测试 reasoning 字段长度需要 10 字符以上",
    }


def _designer_extraction_payload() -> dict:
    return {
        "system_name": "Test System",
        "specialists": [
            {
                "id": "a",
                "name": "Specialist A",
                "description": "处理 A 类业务请求的专员描述",
                "nl_brief": "处理 A 类业务请求并响应用户",
                "handoff_targets": ["b", "c"],
            },
            {
                "id": "b",
                "name": "Specialist B",
                "description": "处理 B 类业务请求的专员描述",
                "nl_brief": "处理 B 类业务请求并响应用户",
                "handoff_targets": ["c"],
            },
            {
                "id": "c",
                "name": "Specialist C",
                "description": "处理 C 类业务请求的专员描述",
                "nl_brief": "处理 C 类业务请求并响应用户",
                "handoff_targets": ["a"],
            },
        ],
        "triage_initial_targets": ["a", "b", "c"],
        "shared_context_fields": [],
    }


def _make_pipeline_for_one_case(industry="01", is_multi=True):
    llm = MagicMock()
    llm.chat = AsyncMock(
        side_effect=[
            ChatMessage(
                role="assistant",
                content=json.dumps(_classification_payload(industry=industry, is_multi=is_multi)),
            ),
            ChatMessage(role="assistant", content=json.dumps(_designer_extraction_payload())),
        ]
    )
    router = MagicMock()
    router.resolve = MagicMock(return_value="test-model")
    return MultiAgentFactoryPipeline(llm_client=llm, model_router=router)


@pytest.mark.asyncio
async def test_runner_passes_when_all_expectations_met():
    pipeline = _make_pipeline_for_one_case(industry="01", is_multi=True)
    runner = MultiAgentEvalRunnerImpl(pipeline=pipeline)
    eval_set = EvalSet(
        asset_id="ES-test",
        version="1.0.0",
        description="test multi-agent eval set",
        cases=[
            EvalCase(
                case_id="t01",
                description="happy path multi-agent test case",
                nl="做一个客服系统包含 A B C 三个专员",
                expected=ExpectedShape(
                    classify_industry="01",
                    classify_scenario="客服",
                    classify_is_multi_agent=True,
                    min_specialists=3,
                    max_specialists=5,
                    require_compose_ok=True,
                ),
            )
        ],
    )

    report = await runner.run(eval_set)
    assert report.total == 1
    assert report.passed == 1
    assert report.case_results[0].passed
    assert report.case_results[0].compose_ok is True
    assert report.case_results[0].actual_specialists == 3


@pytest.mark.asyncio
async def test_runner_fails_when_industry_mismatches():
    pipeline = _make_pipeline_for_one_case(industry="01", is_multi=True)
    runner = MultiAgentEvalRunnerImpl(pipeline=pipeline)
    eval_set = EvalSet(
        asset_id="ES-test",
        version="1.0.0",
        description="industry mismatch test",
        cases=[
            EvalCase(
                case_id="t02",
                description="industry mismatch test case",
                nl="做一个银行客服系统",
                expected=ExpectedShape(
                    classify_industry="02",  # expects finance, gets 01
                    classify_is_multi_agent=True,
                ),
            )
        ],
    )
    report = await runner.run(eval_set)
    assert report.passed == 0
    assert "industry" in report.case_results[0].reasons[0]


@pytest.mark.asyncio
async def test_runner_fails_when_classify_says_single_but_expected_multi():
    """Router classifies single-agent; expectation is multi -> FAIL."""
    pipeline = _make_pipeline_for_one_case(industry="01", is_multi=False)
    runner = MultiAgentEvalRunnerImpl(pipeline=pipeline)
    eval_set = EvalSet(
        asset_id="ES-test",
        version="1.0.0",
        description="classify single but expected multi",
        cases=[
            EvalCase(
                case_id="t03",
                description="classify mismatch test case",
                nl="查销售库写日报推钉钉",
                expected=ExpectedShape(classify_is_multi_agent=True),
            )
        ],
    )
    report = await runner.run(eval_set)
    assert report.passed == 0
    assert any("multi-agent" in r for r in report.case_results[0].reasons)


@pytest.mark.asyncio
async def test_runner_fails_when_specialist_count_below_min():
    """Designer produces 3 specialists but case expects min 5."""
    pipeline = _make_pipeline_for_one_case()
    runner = MultiAgentEvalRunnerImpl(pipeline=pipeline)
    eval_set = EvalSet(
        asset_id="ES-test",
        version="1.0.0",
        description="min specialists test",
        cases=[
            EvalCase(
                case_id="t04",
                description="specialists below min test case",
                nl="做客服系统",
                expected=ExpectedShape(min_specialists=5),
            )
        ],
    )
    report = await runner.run(eval_set)
    assert report.passed == 0
    assert any("specialists" in r for r in report.case_results[0].reasons)


@pytest.mark.asyncio
async def test_runner_handles_classify_exception_gracefully():
    """If classification fails, case is marked failed but runner continues."""
    llm = MagicMock()
    llm.chat = AsyncMock(side_effect=RuntimeError("network exploded"))
    router = MagicMock()
    router.resolve = MagicMock(return_value="test-model")
    # Force LLM client into router so all classify calls raise
    pipeline = MultiAgentFactoryPipeline(llm_client=llm, model_router=router)
    runner = MultiAgentEvalRunnerImpl(pipeline=pipeline)

    eval_set = EvalSet(
        asset_id="ES-test",
        version="1.0.0",
        description="exception handling test",
        cases=[
            EvalCase(
                case_id="t05",
                description="should fail gracefully not crash runner",
                nl="测试需求异常",
                expected=ExpectedShape(classify_is_multi_agent=True),
            )
        ],
    )
    report = await runner.run(eval_set)
    assert report.passed == 0
    assert "classify raised" in report.case_results[0].reasons[0]
    assert report.case_results[0].error is not None
