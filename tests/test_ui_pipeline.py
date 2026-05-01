from __future__ import annotations

import json

import pytest

from app.core.llm.client import ChatMessage
from app.core.pipelines.ui.orchestrator import UIPipeline
from app.core.pipelines.ui.phase2_prototype import PrototypeVariant, generate_prototypes
from app.core.pipelines.ui.phase3_production import generate_production_code
from app.core.stability.contracts import UIBlueprint


class FakeLLM:
    """A deterministic fake LLM for UI pipeline tests."""

    def __init__(self, responses: list[str | Exception]) -> None:
        self._responses = responses
        self.calls: list[dict[str, object]] = []

    async def chat(
        self,
        model: str,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, object]] | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> ChatMessage:
        self.calls.append(
            {
                "model": model,
                "messages": [message.model_copy(deep=True) for message in messages],
                "tools": tools,
                "temperature": temperature,
            }
        )
        response = self._responses[len(self.calls) - 1]
        if isinstance(response, Exception):
            raise response
        return ChatMessage(role="assistant", content=response)


@pytest.fixture(autouse=True)
def patch_retry_timers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable retry sleeps for deterministic tests."""

    async def _no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("app.core.llm.retry.asyncio.sleep", _no_sleep)
    monkeypatch.setattr("app.core.llm.retry.random.uniform", lambda _a, _b: 1.0)


@pytest.fixture
def blueprint() -> UIBlueprint:
    """Return a stable test blueprint."""
    return UIBlueprint.model_validate(
        {
            "pages": [
                {
                    "route": "/",
                    "title": "Overview",
                    "components": ["hero", "card-grid"],
                    "data_fields": ["headline", "metrics"],
                },
                {
                    "route": "/orders",
                    "title": "Orders",
                    "components": ["table", "form"],
                    "data_fields": ["order_id", "status", "owner"],
                },
            ],
            "brand": {"primary": "#0F62FE", "font": "IBM Plex Sans", "radius": "md"},
        }
    )


def _phase2_payload(count: int = 3) -> str:
    variants = [
        {
            "name": f"variant-{index}",
            "description": f"Visual direction {index}",
            "html": f"<!doctype html><html><body><div id='variant-{index}'>Prototype {index}</div></body></html>",
        }
        for index in range(count)
    ]
    return json.dumps({"variants": variants}, ensure_ascii=False)


def _phase3_payload() -> str:
    return json.dumps(
        {
            "files": [
                {"path": "src/main.tsx", "content": "import React from 'react';"},
                {"path": "src/App.tsx", "content": "export function App() { return <div>demo</div>; }"},
            ],
            "dependencies": {"react": "^18.3.1", "react-dom": "^18.3.1"},
            "entrypoint": "src/main.tsx",
        },
        ensure_ascii=False,
    )


@pytest.mark.asyncio
async def test_generate_prototypes_returns_three_variants(blueprint: UIBlueprint) -> None:
    llm = FakeLLM([_phase2_payload()])

    result = await generate_prototypes(llm, model="test-model", blueprint=blueprint)

    assert result.attempts == 1
    assert result.used_fallback is False
    assert len(result.value.variants) == 3


@pytest.mark.asyncio
async def test_generate_production_code_uses_selected_variant(blueprint: UIBlueprint) -> None:
    llm = FakeLLM([_phase3_payload()])
    chosen_variant = PrototypeVariant.model_validate(
        {
            "name": "compact-sidebar",
            "description": "Dense enterprise chrome",
            "html": "<!doctype html><html><body>variant</body></html>",
        }
    )

    result = await generate_production_code(
        llm,
        model="test-model",
        blueprint=blueprint,
        chosen_variant=chosen_variant,
    )

    assert result.used_fallback is False
    assert result.value.entrypoint == "src/main.tsx"
    assert result.value.files[0].path == "src/main.tsx"


@pytest.mark.asyncio
async def test_ui_pipeline_orchestrator_yields_phase_events(blueprint: UIBlueprint) -> None:
    llm = FakeLLM([_phase2_payload(), _phase3_payload()])
    pipeline = UIPipeline(llm, model="test-model")

    events = [event async for event in pipeline.run(blueprint)]

    assert [event.phase for event in events] == ["phase2", "phase3", "completed"]
    assert events[-1].data is not None
    assert events[-1].data["chosen_variant"]["name"] == "variant-0"
    assert events[-1].data["code"]["entrypoint"] == "src/main.tsx"


@pytest.mark.asyncio
async def test_generate_prototypes_repairs_when_variant_count_is_invalid(blueprint: UIBlueprint) -> None:
    llm = FakeLLM([_phase2_payload(count=2), _phase2_payload(count=3)])

    result = await generate_prototypes(llm, model="test-model", blueprint=blueprint)

    second_call_messages = llm.calls[1]["messages"]

    assert result.attempts == 2
    assert len(result.value.variants) == 3
    assert isinstance(second_call_messages, list)
    assert "Previous output failed validation" in second_call_messages[-1].content
