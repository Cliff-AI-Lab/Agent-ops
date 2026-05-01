from __future__ import annotations

import pytest

from app.core.llm.client import ChatMessage
from app.core.llm.errors import RateLimitError
from app.core.llm.profile import ProfileProber


class FakeLLM:
    """A deterministic fake LLM for profile probing tests."""

    def __init__(self, responses: list[str | Exception]) -> None:
        self._responses = responses
        self.call_count = 0

    async def chat(
        self,
        model: str,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, object]] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ChatMessage:
        _ = (model, messages, tools, temperature)
        response = self._responses[self.call_count]
        self.call_count += 1
        if isinstance(response, Exception):
            raise response
        return ChatMessage(role="assistant", content=response)


@pytest.mark.asyncio
async def test_probe_returns_full_scores_when_all_probes_pass(tmp_path) -> None:
    llm = FakeLLM(
        [
            '{"ok": true, "items": ["a", "b"]}',
            '{"tool_ready": true}',
            "LONG_CONTEXT_OK",
            "这是一个包含首页和表单的自然中文短句。",
        ]
    )
    prober = ProfileProber(llm, db_path=str(tmp_path / "profiles.db"))

    profile = await prober.probe("claude-sonnet-4")

    assert profile.family == "sonnet"
    assert profile.json_reliability == pytest.approx(1.0)
    assert profile.function_calling_ok is True
    assert profile.long_context_ok is True
    assert profile.chinese_quality == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_probe_marks_schema_reminder_when_json_probe_fails(tmp_path) -> None:
    llm = FakeLLM(
        [
            "not-json",
            '{"tool_ready": true}',
            "LONG_CONTEXT_OK",
            "这是一个包含首页和表单的自然中文短句。",
        ]
    )
    prober = ProfileProber(llm, db_path=str(tmp_path / "profiles.db"))

    profile = await prober.probe("model-x")

    assert profile.json_reliability == 0.0
    assert profile.needs_schema_reminder is True


@pytest.mark.asyncio
async def test_probe_uses_cache_without_recalling_llm(tmp_path) -> None:
    llm = FakeLLM(
        [
            '{"ok": true, "items": ["a", "b"]}',
            '{"tool_ready": true}',
            "LONG_CONTEXT_OK",
            "这是一个包含首页和表单的自然中文短句。",
        ]
    )
    prober = ProfileProber(llm, db_path=str(tmp_path / "profiles.db"))

    first = await prober.probe("claude-haiku-3")
    second = await prober.probe("claude-haiku-3")

    assert first.model_id == second.model_id
    assert llm.call_count == 4


@pytest.mark.asyncio
async def test_probe_keeps_partial_scores_when_rate_limited_mid_run(tmp_path) -> None:
    llm = FakeLLM(
        [
            '{"ok": true, "items": ["a", "b"]}',
            RateLimitError("rate limited", retry_after=1.0),
            "LONG_CONTEXT_OK",
            "这是一个包含首页和表单的自然中文短句。",
        ]
    )
    prober = ProfileProber(llm, db_path=str(tmp_path / "profiles.db"))

    profile = await prober.probe("model-y")

    assert profile.json_reliability == pytest.approx(1.0)
    assert profile.function_calling_ok is False
    assert profile.long_context_ok is True


@pytest.mark.asyncio
async def test_probe_keeps_unknown_family_for_non_family_model_names(tmp_path) -> None:
    llm = FakeLLM(
        [
            '{"ok": true, "items": ["a", "b"]}',
            '{"tool_ready": true}',
            "LONG_CONTEXT_OK",
            "这是一个包含首页和表单的自然中文短句。",
        ]
    )
    prober = ProfileProber(llm, db_path=str(tmp_path / "profiles.db"))

    profile = await prober.probe("gpt-4o")

    assert profile.family == "unknown"
