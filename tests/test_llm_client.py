from __future__ import annotations

import httpx
import pytest

from app.config import get_settings
from app.core.llm.client import ChatMessage, LLMClient
from app.core.llm.errors import ConfigError, InvalidResponseError
from app.core.llm import retry as retry_module


@pytest.mark.asyncio
async def test_list_models_filters_non_chat_models(
    gateway_base_url: str,
    respx_mock,
) -> None:
    route = respx_mock.get(f"{gateway_base_url}/models").mock(
        return_value=httpx.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {"id": "gpt-4o-mini", "object": "model"},
                    {"id": "text-embedding-3-large", "object": "model"},
                    {"id": "whisper-1", "object": "model"},
                    {"id": "gpt-4.1", "object": "model"},
                    {"id": "tts-1", "object": "model"},
                ],
            },
        )
    )

    client = LLMClient()
    try:
        models = await client.list_models()
    finally:
        await client.aclose()

    assert route.call_count == 1
    assert [model.id for model in models] == ["gpt-4o-mini", "gpt-4.1"]
    assert all(model.is_chat for model in models)


@pytest.mark.asyncio
async def test_chat_retries_on_rate_limit_with_exponential_backoff(
    gateway_base_url: str,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock,
) -> None:
    sleep_calls: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    monkeypatch.setattr(retry_module.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(retry_module.random, "uniform", lambda _a, _b: 1.0)

    route = respx_mock.post(f"{gateway_base_url}/chat/completions").mock(
        side_effect=[
            httpx.Response(
                429,
                json={
                    "error": {
                        "message": "rate limited",
                        "type": "rate_limit_error",
                        "param": None,
                        "code": None,
                    }
                },
                headers={"retry-after": "1"},
            ),
            httpx.Response(
                200,
                json={
                    "id": "chatcmpl_1",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "gpt-4o-mini",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "done"},
                            "finish_reason": "stop",
                        }
                    ],
                },
            ),
        ]
    )

    client = LLMClient(max_retries=2)
    try:
        message = await client.chat(
            "gpt-4o-mini",
            [ChatMessage(role="user", content="hello")],
        )
    finally:
        await client.aclose()

    assert route.call_count == 2
    assert sleep_calls == [0.5]
    assert message.content == "done"


@pytest.mark.asyncio
async def test_chat_raises_invalid_response_without_retry_on_bad_request(
    gateway_base_url: str,
    respx_mock,
) -> None:
    route = respx_mock.post(f"{gateway_base_url}/chat/completions").mock(
        return_value=httpx.Response(
            400,
            json={
                "error": {
                    "message": "invalid request",
                    "type": "invalid_request_error",
                    "param": None,
                    "code": None,
                }
            },
        )
    )

    client = LLMClient(max_retries=3)
    try:
        with pytest.raises(InvalidResponseError):
            await client.chat("gpt-4o-mini", [ChatMessage(role="user", content="bad")])
    finally:
        await client.aclose()

    assert route.call_count == 1


def test_client_raises_config_error_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RUIDONG_API_KEY", raising=False)
    get_settings.cache_clear()

    with pytest.raises(ConfigError):
        LLMClient()
