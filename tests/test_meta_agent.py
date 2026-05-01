from __future__ import annotations

import pytest

from app.core.llm.client import ChatMessage
from app.core.orchestrator.meta_agent import MetaAgent


class FakeLLM:
    """A deterministic fake LLM for meta-agent tests."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = responses
        self.call_count = 0

    async def chat(
        self,
        model: str,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, object]] | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> ChatMessage:
        _ = (model, messages, tools, temperature)
        response = self._responses[self.call_count]
        self.call_count += 1
        return ChatMessage(role="assistant", content=response)


@pytest.mark.asyncio
async def test_classify_intent_returns_ui_for_ui_prompt() -> None:
    agent = MetaAgent(
        FakeLLM(['{"intent":"ui","confidence":0.91}']),
        light_model="gpt-4o-mini",
        heavy_model="gpt-4o",
    )

    result = await agent.classify_intent("帮我生成一个数据看板 UI", [])

    assert result.intent == "ui"
    assert result.confidence == pytest.approx(0.91)


@pytest.mark.asyncio
async def test_build_spec_returns_valid_requirement_spec() -> None:
    agent = MetaAgent(
        FakeLLM(
            [
                (
                    '{"product_name":"销售看板","target_users":["销售"],"core_pages":["首页"],'
                    '"reference_brands":[],"special_requirements":["中文"]}'
                )
            ]
        ),
        light_model="gpt-4o-mini",
        heavy_model="gpt-4o",
    )

    spec = await agent.build_spec("帮我做一个销售看板", ["需要中文"])

    assert spec.product_name == "销售看板"
    assert spec.target_users == ["销售"]


@pytest.mark.asyncio
async def test_build_spec_repairs_invalid_first_response() -> None:
    agent = MetaAgent(
        FakeLLM(
            [
                "not-json",
                (
                    '{"product_name":"客服工作台","target_users":["客服"],"core_pages":["首页"],'
                    '"reference_brands":[],"special_requirements":[]}'
                ),
            ]
        ),
        light_model="gpt-4o-mini",
        heavy_model="gpt-4o",
    )

    spec = await agent.build_spec("做一个客服工作台", [])

    assert spec.product_name == "客服工作台"


@pytest.mark.asyncio
async def test_classify_intent_returns_unknown_for_low_confidence() -> None:
    agent = MetaAgent(
        FakeLLM(['{"intent":"ui","confidence":0.3}']),
        light_model="gpt-4o-mini",
        heavy_model="gpt-4o",
    )

    result = await agent.classify_intent("也许做个东西吧", [])

    assert result.intent == "unknown"
    assert result.confidence < 0.5
