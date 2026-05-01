"""Tests for IntentParserImpl with mocked LLM."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.llm.client import ChatMessage
from app.core.pipelines.factory.intent_parser import IntentParser, IntentParserImpl
from app.core.pipelines.factory.ir import StructuredIntent


def _mock_router(model: str = "test-model"):
    router = MagicMock()
    router.resolve = MagicMock(return_value=model)
    return router


def _llm_returning(text: str):
    llm = MagicMock()
    llm.chat = AsyncMock(return_value=ChatMessage(role="assistant", content=text))
    return llm


def test_interface_importable():
    assert IntentParser is not None


def test_impl_constructable():
    parser = IntentParserImpl(llm_client=None, model_router=_mock_router())
    assert parser is not None


@pytest.mark.asyncio
async def test_empty_input_rejected():
    parser = IntentParserImpl(llm_client=_llm_returning("{}"), model_router=_mock_router())
    with pytest.raises(ValueError, match="non-empty"):
        await parser.parse("")
    with pytest.raises(ValueError, match="non-empty"):
        await parser.parse("   ")


@pytest.mark.asyncio
async def test_parse_happy_path():
    payload = {
        "schema_version": "1.0",
        "goal": "测试",
        "trigger": {"type": "manual"},
        "steps": [
            {
                "id": "s1",
                "verb": "查询数据库",
                "expected_output_kind": "tabular_data",
                "suggested_subcategory": "DB",
            }
        ],
        "outputs": [{"name": "out", "type": "json"}],
        "raw_user_input": "测试查询",
    }
    parser = IntentParserImpl(
        llm_client=_llm_returning(json.dumps(payload)),
        model_router=_mock_router(),
    )
    intent = await parser.parse("测试查询")
    assert isinstance(intent, StructuredIntent)
    assert intent.goal == "测试"
    assert len(intent.steps) == 1
    assert intent.steps[0].verb == "查询数据库"


@pytest.mark.asyncio
async def test_parse_strips_markdown_fences():
    payload = {
        "schema_version": "1.0",
        "goal": "测试",
        "trigger": {"type": "manual"},
        "steps": [],
        "outputs": [],
        "raw_user_input": "x",
    }
    fenced = f"```json\n{json.dumps(payload)}\n```"
    parser = IntentParserImpl(
        llm_client=_llm_returning(fenced),
        model_router=_mock_router(),
    )
    intent = await parser.parse("x")
    assert intent.goal == "测试"


@pytest.mark.asyncio
async def test_parse_repairs_on_validation_error():
    """First call returns malformed; second call returns valid -> should succeed."""
    bad = "{not even json"
    good_payload = {
        "schema_version": "1.0",
        "goal": "fixed",
        "trigger": {"type": "manual"},
        "steps": [],
        "outputs": [],
        "raw_user_input": "test",
    }
    llm = MagicMock()
    llm.chat = AsyncMock(
        side_effect=[
            ChatMessage(role="assistant", content=bad),
            ChatMessage(role="assistant", content=json.dumps(good_payload)),
        ]
    )
    parser = IntentParserImpl(llm_client=llm, model_router=_mock_router())
    intent = await parser.parse("test")
    assert intent.goal == "fixed"
    assert llm.chat.await_count == 2


@pytest.mark.asyncio
async def test_parse_fails_after_max_attempts():
    """All attempts return malformed -> raise."""
    llm = MagicMock()
    llm.chat = AsyncMock(
        return_value=ChatMessage(role="assistant", content="not json at all")
    )
    parser = IntentParserImpl(
        llm_client=llm, model_router=_mock_router(), max_repair_attempts=1
    )
    with pytest.raises(ValueError, match="failed after"):
        await parser.parse("test")
    assert llm.chat.await_count == 2  # 1 + 1 repair


@pytest.mark.asyncio
async def test_parse_injects_raw_user_input_if_missing():
    """If LLM forgets raw_user_input, parser fills it in."""
    payload_missing_raw = {
        "schema_version": "1.0",
        "goal": "测试",
        "trigger": {"type": "manual"},
        "steps": [],
        "outputs": [],
    }
    parser = IntentParserImpl(
        llm_client=_llm_returning(json.dumps(payload_missing_raw)),
        model_router=_mock_router(),
    )
    intent = await parser.parse("我的原始需求")
    assert intent.raw_user_input == "我的原始需求"
