"""End-to-end Phase 7 multi-agent factory pipeline tests."""
from __future__ import annotations

import ast
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.llm.client import ChatMessage
from app.core.pipelines.factory.multi_agent_pipeline import (
    MultiAgentFactoryPipeline,
    MultiAgentNotApplicable,
    _slug,
)


# ---- helpers ---------------------------------------------------------------


def _classification_payload_multi() -> dict:
    return {
        "industry_code": "01",
        "primary": "通用",
        "sub": None,
        "business_scenario": "客服",
        "is_multi_agent": True,
        "confidence": 0.92,
        "reasoning": "航司客服需要 triage + 多 specialist 协调（订票/退票/座位/FAQ）",
    }


def _classification_payload_single() -> dict:
    return {
        "industry_code": "01",
        "primary": "通用",
        "sub": None,
        "business_scenario": "数据日报",
        "is_multi_agent": False,
        "confidence": 0.95,
        "reasoning": "单 ETL 工作流场景，无需多 agent 协调",
    }


def _designer_extraction_payload() -> dict:
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
                "name": "Booking",
                "description": "处理订票、改签、退票申请",
                "nl_brief": "订票改签退票相关请求处理",
                "handoff_targets": ["seat", "refunds"],
            },
            {
                "id": "seat",
                "name": "Seat Services",
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
                "name": "Refunds",
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
        ],
    }


def _make_pipeline_with_mocked_llm() -> MultiAgentFactoryPipeline:
    """Mock LLM serves: 1 classification call + 1 designer extraction call."""
    llm = MagicMock()
    llm.chat = AsyncMock(
        side_effect=[
            ChatMessage(role="assistant", content=json.dumps(_classification_payload_multi())),
            ChatMessage(role="assistant", content=json.dumps(_designer_extraction_payload())),
        ]
    )
    router = MagicMock()
    router.resolve = MagicMock(return_value="test-model")
    return MultiAgentFactoryPipeline(llm_client=llm, model_router=router)


def _make_pipeline_classifying_single() -> MultiAgentFactoryPipeline:
    llm = MagicMock()
    llm.chat = AsyncMock(
        side_effect=[
            ChatMessage(role="assistant", content=json.dumps(_classification_payload_single())),
        ]
    )
    router = MagicMock()
    router.resolve = MagicMock(return_value="test-model")
    return MultiAgentFactoryPipeline(llm_client=llm, model_router=router)


# ---- _slug helper -----------------------------------------------------------


def test_slug_basic():
    assert _slug("Airline Customer Service System") == "airline_customer_service_system"


def test_slug_strips_punctuation():
    assert _slug("System.with-mixed/chars!") == "system_with_mixed_chars"


def test_slug_handles_leading_digit():
    assert _slug("123 system") == "_123_system"


def test_slug_truncates_long():
    long_input = "a" * 100
    assert len(_slug(long_input)) <= 48


def test_slug_empty_fallback():
    assert _slug("") == "system"


# ---- build() ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_e2e_airline_cs():
    """NL -> classification -> spec -> compiled Python source."""
    pipeline = _make_pipeline_with_mocked_llm()
    result = await pipeline.build(
        "做一个航司客服系统，包含订票、退票、选座、行李 FAQ、补偿处理多个专员"
    )

    # Stage 1 output: classification
    assert result["classification"].is_multi_agent is True
    assert result["classification"].industry_code == "01"
    assert result["classification"].business_scenario == "客服"

    # Stage 2 output: spec
    spec = result["spec"]
    assert spec.name == "Airline Customer Service System"
    assert len(spec.specialists) == 5
    assert spec.runtime == "openai_agents_sdk"
    assert spec.validate_graph() == []

    # Stage 3 output: source code
    src = result["source_code"]
    assert isinstance(src, str)
    assert "from agents import Agent" in src
    assert "ENTRY_AGENT = triage_agent" in src
    # AST self-check round-trip
    ast.parse(src)

    # System slug
    assert result["system_slug"] == "airline_customer_service_system"


@pytest.mark.asyncio
async def test_build_rejects_single_agent_nl():
    """When Router classifies is_multi_agent=False, raise MultiAgentNotApplicable."""
    pipeline = _make_pipeline_classifying_single()
    with pytest.raises(MultiAgentNotApplicable, match="is_multi_agent=False"):
        await pipeline.build("每周一查销售库写日报推钉钉")


@pytest.mark.asyncio
async def test_build_rejects_empty_nl():
    pipeline = _make_pipeline_with_mocked_llm()
    with pytest.raises(ValueError, match="empty"):
        await pipeline.build("")


# ---- deploy() ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_deploy_writes_three_files(tmp_path: Path):
    pipeline = _make_pipeline_with_mocked_llm()
    result = await pipeline.build("做一个航司客服多 agent 系统")

    paths = pipeline.deploy(result, tmp_path)

    assert paths["main"].exists()
    assert paths["spec"].exists()
    assert paths["manifest"].exists()

    # main.py is valid Python
    src = paths["main"].read_text(encoding="utf-8")
    ast.parse(src)
    assert "ENTRY_AGENT = triage_agent" in src

    # spec.json round-trips
    spec_data = json.loads(paths["spec"].read_text(encoding="utf-8"))
    assert spec_data["name"] == "Airline Customer Service System"
    assert spec_data["runtime"] == "openai_agents_sdk"

    # manifest.json has expected fields
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    assert manifest["system_slug"] == "airline_customer_service_system"
    assert manifest["industry"]["code"] == "01"
    assert manifest["specialist_count"] == 5
    assert manifest["runtime"] == "openai_agents_sdk"
    assert "built_at" in manifest


@pytest.mark.asyncio
async def test_deploy_creates_named_subdir(tmp_path: Path):
    pipeline = _make_pipeline_with_mocked_llm()
    result = await pipeline.build("做一个航司客服多 agent 系统")
    pipeline.deploy(result, tmp_path)
    target_dir = tmp_path / "airline_customer_service_system"
    assert target_dir.is_dir()
    assert {p.name for p in target_dir.iterdir()} == {"main.py", "spec.json", "manifest.json"}


def test_deploy_validates_input():
    """deploy() refuses dict missing source_code/spec."""
    pipeline = MultiAgentFactoryPipeline(
        router=MagicMock(),
        designer=MagicMock(),
        composer=MagicMock(),
    )
    with pytest.raises(ValueError, match="missing source_code"):
        pipeline.deploy({}, Path("/tmp"))


# ---- with optional resolvers (W2 enhancement) -------------------------------


class _FakePrompt:
    def __init__(self, template: str) -> None:
        self.template = template


class _FakeAtom:
    def __init__(self, subcategory: str) -> None:
        self.subcategory = subcategory


@pytest.mark.asyncio
async def test_build_with_prompt_atom_dicts(tmp_path: Path):
    """Pipeline forwards prompts/atoms to Composer; resolved values appear in src."""
    llm = MagicMock()
    llm.chat = AsyncMock(
        side_effect=[
            ChatMessage(role="assistant", content=json.dumps(_classification_payload_multi())),
            ChatMessage(role="assistant", content=json.dumps(_designer_extraction_payload())),
        ]
    )
    router = MagicMock()
    router.resolve = MagicMock(return_value="test-model")

    pipeline = MultiAgentFactoryPipeline(
        llm_client=llm,
        model_router=router,
        prompts={"prompt.triage.general.v1": _FakePrompt(
            template="Route every user turn to the most relevant airline specialist."
        )},
        atoms={"atom.http.generic.v1": _FakeAtom(subcategory="HTTP")},
    )
    result = await pipeline.build("做一个航司客服系统")
    src = result["source_code"]
    assert "Route every user turn" in src
    # AST still valid
    ast.parse(src)
