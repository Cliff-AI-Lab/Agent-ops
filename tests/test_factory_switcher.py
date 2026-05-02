"""Tests for FactorySwitcher - V2.1.0 W2 final block."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.llm.client import ChatMessage
from app.core.pipelines.factory.industry.interface import IndustryClassification
from app.core.pipelines.factory.multi_agent_pipeline import MultiAgentFactoryPipeline
from app.core.pipelines.factory.pipeline import FactoryPipeline
from app.core.pipelines.factory.switcher import FactorySwitcher


# ---- helpers ---------------------------------------------------------------


ATOMS_DIR = Path(__file__).resolve().parents[1] / "capabilities" / "atom"


def _classification_multi_payload() -> dict:
    return {
        "industry_code": "01",
        "primary": "通用",
        "sub": None,
        "business_scenario": "客服",
        "is_multi_agent": True,
        "confidence": 0.92,
        "reasoning": "航司客服需要 triage + 多 specialist 协调（订票/退票）",
    }


def _classification_single_payload() -> dict:
    return {
        "industry_code": "01",
        "primary": "通用",
        "sub": None,
        "business_scenario": "数据日报",
        "is_multi_agent": False,
        "confidence": 0.95,
        "reasoning": "单 ETL 工作流场景，无需多 agent 协调",
    }


def _designer_payload() -> dict:
    return {
        "system_name": "Test CS System",
        "specialists": [
            {
                "id": "a",
                "name": "Specialist A",
                "description": "处理 A 类业务请求的专员",
                "nl_brief": "处理 A 类业务请求并响应用户",
                "handoff_targets": ["b"],
            },
            {
                "id": "b",
                "name": "Specialist B",
                "description": "处理 B 类业务请求的专员",
                "nl_brief": "处理 B 类业务请求并响应用户",
                "handoff_targets": ["a"],
            },
        ],
        "triage_initial_targets": ["a", "b"],
        "shared_context_fields": [],
    }


# ---- multi-agent path ------------------------------------------------------


@pytest.mark.asyncio
async def test_routes_to_multi_when_classification_multi():
    """When Router classifies is_multi_agent=True, Switcher uses multi pipeline."""
    llm = MagicMock()
    llm.chat = AsyncMock(
        side_effect=[
            ChatMessage(role="assistant", content=json.dumps(_classification_multi_payload())),
            ChatMessage(role="assistant", content=json.dumps(_designer_payload())),
        ]
    )
    model_router = MagicMock()
    model_router.resolve = MagicMock(return_value="test-model")

    switcher = FactorySwitcher(llm_client=llm, model_router=model_router)
    result = await switcher.build("做一个客服多 agent 系统")

    assert result["path"] == "multi"
    assert result["classification"].is_multi_agent is True
    assert "spec" in result
    assert "source_code" in result
    assert "system_slug" in result
    assert len(result["spec"].specialists) == 2


@pytest.mark.asyncio
async def test_routes_to_multi_only_one_classify_call():
    """Switcher classifies once; multi pipeline doesn't re-classify (avoids double LLM call)."""
    llm = MagicMock()
    llm.chat = AsyncMock(
        side_effect=[
            ChatMessage(role="assistant", content=json.dumps(_classification_multi_payload())),
            ChatMessage(role="assistant", content=json.dumps(_designer_payload())),
        ]
    )
    model_router = MagicMock()
    model_router.resolve = MagicMock(return_value="test-model")

    switcher = FactorySwitcher(llm_client=llm, model_router=model_router)
    await switcher.build("做一个客服多 agent 系统")

    # Only 2 LLM calls: 1 classify + 1 designer extract. NOT 3 (= 2 classifies + 1 extract).
    assert llm.chat.call_count == 2


# ---- single-agent path -----------------------------------------------------


@pytest.mark.asyncio
async def test_routes_to_single_when_classification_single():
    """When Router classifies is_multi_agent=False, Switcher uses single FactoryPipeline."""
    llm = MagicMock()
    # Switcher classify + FactoryPipeline.build internal calls (intent + 2 ranks):
    llm.chat = AsyncMock(
        side_effect=[
            # 1. Switcher's router.classify
            ChatMessage(role="assistant", content=json.dumps(_classification_single_payload())),
            # 2. FactoryPipeline IntentParser
            ChatMessage(
                role="assistant",
                content=json.dumps(
                    {
                        "schema_version": "1.0",
                        "goal": "测试目标 - 数据日报",
                        "trigger": {"type": "manual"},
                        "steps": [
                            {
                                "id": "s1",
                                "verb": "查询数据库",
                                "inputs": {},
                                "expected_output_kind": "tabular_data",
                                "constraints": {},
                                "suggested_subcategory": "DB",
                            }
                        ],
                        "outputs": [{"name": "out", "type": "void"}],
                        "constraints": {"language": "zh"},
                        "raw_user_input": "test",
                    }
                ),
            ),
            # 3. Resolver rank for s1 (single candidate path may or may not call LLM)
            ChatMessage(
                role="assistant",
                content=json.dumps(
                    {
                        "asset_id": "atom.db.postgres.v1",
                        "confidence": 0.85,
                        "reason": "唯一 DB 候选",
                    }
                ),
            ),
        ]
    )
    model_router = MagicMock()
    model_router.resolve = MagicMock(return_value="test-model")

    single = FactoryPipeline(
        atoms_dir=ATOMS_DIR, llm_client=llm, model_router=model_router
    )
    switcher = FactorySwitcher(
        single_pipeline=single, llm_client=llm, model_router=model_router
    )
    result = await switcher.build("查销售库写日报")

    assert result["path"] == "single"
    assert result["classification"].is_multi_agent is False
    # Single pipeline's contract fields
    assert "intent" in result
    assert "dag" in result
    assert "outputs" in result


# ---- error paths -----------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_nl_rejected():
    switcher = FactorySwitcher()
    with pytest.raises(ValueError, match="empty"):
        await switcher.build("")


@pytest.mark.asyncio
async def test_single_path_without_pipeline_raises():
    """Switcher refuses single path if single_pipeline not injected."""
    llm = MagicMock()
    llm.chat = AsyncMock(
        return_value=ChatMessage(
            role="assistant",
            content=json.dumps(_classification_single_payload()),
        )
    )
    model_router = MagicMock()
    model_router.resolve = MagicMock(return_value="test-model")

    switcher = FactorySwitcher(llm_client=llm, model_router=model_router)
    with pytest.raises(RuntimeError, match="single_pipeline injected"):
        await switcher.build("查销售库写日报")


# ---- contract: multi result has no nested 'classification' duplicate -------


@pytest.mark.asyncio
async def test_multi_result_classification_at_top_level_only():
    """Switcher's multi result has classification once (not nested under another key)."""
    llm = MagicMock()
    llm.chat = AsyncMock(
        side_effect=[
            ChatMessage(role="assistant", content=json.dumps(_classification_multi_payload())),
            ChatMessage(role="assistant", content=json.dumps(_designer_payload())),
        ]
    )
    model_router = MagicMock()
    model_router.resolve = MagicMock(return_value="test-model")
    switcher = FactorySwitcher(llm_client=llm, model_router=model_router)
    result = await switcher.build("做一个客服系统")

    # classification only at top level
    assert isinstance(result["classification"], IndustryClassification)
    # No nested duplicate under spec or elsewhere
    assert not isinstance(result.get("spec"), dict) or "classification" not in result["spec"]
