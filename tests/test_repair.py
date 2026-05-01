from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.llm.client import ChatMessage
from app.core.llm.errors import RateLimitError
from app.core.stability.contracts import RequirementSpec
from app.core.stability.repair import generate_with_repair
from app.core.stability.validator import SchemaValidator


class FakeLLM:
    """A deterministic fake LLM for repair tests."""

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


@pytest.mark.asyncio
async def test_generate_with_repair_accepts_valid_json_on_first_attempt() -> None:
    llm = FakeLLM(
        [
            (
                '{"product_name":"Ops Board","target_users":["ops"],"core_pages":["dashboard"],'
                '"reference_brands":[],"special_requirements":[]}'
            )
        ]
    )

    result = await generate_with_repair(
        llm,
        model="gpt-4o-mini",
        system_prompt="Produce a requirement spec.",
        user_prompt="Build an operations dashboard.",
        schema=RequirementSpec,
    )

    assert result.attempts == 1
    assert result.used_fallback is False
    assert result.value.product_name == "Ops Board"


@pytest.mark.asyncio
async def test_generate_with_repair_retries_after_validation_error() -> None:
    llm = FakeLLM(
        [
            "not-json",
            (
                '{"product_name":"Clinic Intake","target_users":["staff"],"core_pages":["queue"],'
                '"reference_brands":[],"special_requirements":["privacy notice"]}'
            ),
        ]
    )

    result = await generate_with_repair(
        llm,
        model="gpt-4o-mini",
        system_prompt="Produce a requirement spec.",
        user_prompt="Need a clinic queue product.",
        schema=RequirementSpec,
    )

    second_call_messages = llm.calls[1]["messages"]

    assert result.attempts == 2
    assert result.used_fallback is False
    assert isinstance(second_call_messages, list)
    assert "Previous output failed validation" in second_call_messages[-1].content


@pytest.mark.asyncio
async def test_generate_with_repair_uses_fallback_after_exhausting_attempts() -> None:
    llm = FakeLLM(["{}", "[]", "still-bad"])

    result = await generate_with_repair(
        llm,
        model="gpt-4o-mini",
        system_prompt="Produce a requirement spec.",
        user_prompt="Need a spec.",
        schema=RequirementSpec,
        max_repairs=3,
    )

    assert result.attempts == 3
    assert result.used_fallback is True
    assert result.value.product_name == "Fallback Product"
    assert len(result.last_errors) == 3


@pytest.mark.asyncio
async def test_generate_with_repair_propagates_rate_limit_without_repair() -> None:
    llm = FakeLLM([RateLimitError("slow down", retry_after=1.0)])

    with pytest.raises(RateLimitError):
        await generate_with_repair(
            llm,
            model="gpt-4o-mini",
            system_prompt="Produce a requirement spec.",
            user_prompt="Need a spec.",
            schema=RequirementSpec,
        )

    assert len(llm.calls) == 1


def test_schema_validator_format_error_is_short() -> None:
    validator = SchemaValidator(RequirementSpec)

    with pytest.raises(ValidationError) as error_info:
        validator.parse('{"product_name":1}')

    summary = validator.format_error(error_info.value)

    assert len(summary) <= 400
    assert "target_users" in summary or "core_pages" in summary
