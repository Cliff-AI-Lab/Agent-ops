from __future__ import annotations

import json

import pytest

from app.core.llm.client import ChatMessage
from app.core.orchestrator.blueprint_planner import BlueprintPlanner
from app.core.stability.contracts import RequirementSpec, UIBlueprint


class FakeLLM:
    """Canned-response fake LLM for planner tests."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def chat(
        self,
        model: str,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, object]] | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> ChatMessage:
        self.calls.append({"model": model, "messages": list(messages),
                           "temperature": temperature, "max_tokens": max_tokens})
        content = self._responses.pop(0) if self._responses else "{}"
        return ChatMessage(role="assistant", content=content)


VALID_BLUEPRINT_JSON = json.dumps({
    "pages": [
        {"route": "/", "title": "任务列表",
         "components": ["hero", "table"],
         "data_fields": ["title", "assignee", "status"]},
        {"route": "/task-detail", "title": "任务详情",
         "components": ["form"],
         "data_fields": ["title", "description", "due_date"]},
    ],
    "brand": {"primary": "#6366f1", "font": "Inter", "radius": "md"},
})


@pytest.fixture
def sample_spec() -> RequirementSpec:
    return RequirementSpec(
        product_name="团队任务看板",
        target_users=["团队成员", "管理者"],
        core_pages=["任务列表", "任务详情"],
        reference_brands=[],
        special_requirements=["按状态筛选"],
    )


@pytest.mark.asyncio
async def test_plan_returns_valid_blueprint_on_first_attempt(sample_spec) -> None:
    llm = FakeLLM([VALID_BLUEPRINT_JSON])
    planner = BlueprintPlanner(llm, model="test")
    result = await planner.plan(sample_spec)
    assert result.used_fallback is False
    assert result.attempts == 1
    assert isinstance(result.value, UIBlueprint)
    assert len(result.value.pages) == 2
    assert result.value.pages[0].route == "/"


@pytest.mark.asyncio
async def test_plan_repairs_after_invalid_first_response(sample_spec) -> None:
    invalid = '{"pages": [], "brand": {"primary": "not-hex", "font": "Inter", "radius": "md"}}'
    llm = FakeLLM([invalid, VALID_BLUEPRINT_JSON])
    planner = BlueprintPlanner(llm, model="test")
    result = await planner.plan(sample_spec)
    assert result.used_fallback is False
    assert result.attempts == 2
    assert len(result.value.pages) == 2
    # second call should include error feedback in messages
    second_msgs = llm.calls[1]["messages"]
    assert any("failed validation" in m.content for m in second_msgs)


@pytest.mark.asyncio
async def test_plan_falls_back_when_all_attempts_fail(sample_spec) -> None:
    llm = FakeLLM(["bad"] * 5)
    planner = BlueprintPlanner(llm, model="test")
    result = await planner.plan(sample_spec)
    assert result.used_fallback is True
    assert result.attempts == 3
    # fallback template must be a valid UIBlueprint
    assert isinstance(result.value, UIBlueprint)
