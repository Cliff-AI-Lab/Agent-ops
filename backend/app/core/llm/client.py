from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any, Literal

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI, BadRequestError
from openai import RateLimitError as OpenAIRateLimitError
from pydantic import BaseModel

from app.config import get_settings
from app.core.llm.errors import ConfigError, InvalidResponseError, LLMError, NetworkError, RateLimitError
from app.core.llm.model_filter import is_chat_model
from app.core.llm.retry import with_retry
from app.core.trace.bus import emit
import time as _time


class ChatMessage(BaseModel):
    """A simplified chat message contract."""

    role: Literal["system", "user", "assistant", "tool"]
    content: str


class ModelInfo(BaseModel):
    """A simplified model descriptor."""

    id: str
    is_chat: bool


class LLMClient:
    """OpenAI-compatible async client bound to the configured gateway."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 300.0,
        max_retries: int = 3,
    ) -> None:
        settings = get_settings()
        resolved_base_url = base_url or settings.sandbox_api_base
        resolved_api_key = api_key or settings.ruidong_api_key
        if not resolved_api_key:
            raise ConfigError("RUIDONG_API_KEY is required.")
        if not resolved_base_url:
            raise ConfigError("SANDBOX_API_BASE is required.")

        self._max_retries = max_retries
        self._client = AsyncOpenAI(
            base_url=resolved_base_url,
            api_key=resolved_api_key,
            timeout=timeout,
            max_retries=0,
        )

    async def list_models(self) -> list[ModelInfo]:
        """List chat-capable models after client-side filtering."""
        try:
            response = await self._client.models.list()
        except Exception as exc:
            raise self._translate_error(exc) from exc

        models: list[ModelInfo] = []
        for model in getattr(response, "data", []):
            model_id = getattr(model, "id", "")
            if is_chat_model(model_id):
                models.append(ModelInfo(id=model_id, is_chat=True))
        return models

    async def chat(
        self,
        model: str,
        messages: Sequence[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> ChatMessage:
        """Send a chat completion request and return a text response."""

        emit("L1", "LLMClient", "request",
             f"chat(model={model}, msgs={len(messages)}, temp={temperature}, max_tokens={max_tokens or '-'})")
        t0 = _time.time()

        async def _request() -> Any:
            try:
                kwargs: dict[str, Any] = dict(
                    model=model,
                    messages=[message.model_dump() for message in messages],
                    tools=tools,
                    temperature=temperature,
                    stream=False,
                )
                if max_tokens is not None:
                    kwargs["max_tokens"] = max_tokens
                return await self._client.chat.completions.create(**kwargs)
            except Exception as exc:
                raise self._translate_error(exc) from exc

        try:
            response = await with_retry(
                _request,
                max_retries=self._max_retries,
                base_delay=0.5,
                max_delay=8.0,
            )
        except LLMError as err:
            emit("L1", "LLMClient", "error",
                 f"← {type(err).__name__}: {str(err)[:120]}",
                 data={"elapsed_ms": int((_time.time() - t0) * 1000)})
            raise

        parsed = self._parse_chat_message(response)
        emit("L1", "LLMClient", "response",
             f"← {len(parsed.content)} chars",
             data={"elapsed_ms": int((_time.time() - t0) * 1000),
                   "content_len": len(parsed.content)})
        return parsed

    async def chat_stream(
        self,
        model: str,
        messages: Sequence[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream chat completion chunks as dictionaries."""

        async def _request() -> Any:
            try:
                return await self._client.chat.completions.create(
                    model=model,
                    messages=[message.model_dump() for message in messages],
                    tools=tools,
                    stream=True,
                )
            except Exception as exc:
                raise self._translate_error(exc) from exc

        stream = await with_retry(
            _request,
            max_retries=self._max_retries,
            base_delay=0.5,
            max_delay=8.0,
        )

        try:
            async for chunk in stream:
                if hasattr(chunk, "model_dump"):
                    yield chunk.model_dump()
                elif isinstance(chunk, dict):
                    yield chunk
                else:
                    raise InvalidResponseError("Unexpected stream chunk shape.")
        except Exception as exc:
            raise self._translate_error(exc) from exc

    async def aclose(self) -> None:
        """Close the underlying async client."""
        await self._client.close()

    def _parse_chat_message(self, response: Any) -> ChatMessage:
        """Convert an OpenAI SDK response into ChatMessage."""
        try:
            choice = response.choices[0]
            message = choice.message
            content = message.content
            role = message.role or "assistant"
        except (AttributeError, IndexError, KeyError, TypeError) as exc:
            raise InvalidResponseError("Invalid chat completion payload.") from exc

        if not isinstance(content, str):
            raise InvalidResponseError("Chat completion content must be a string.")

        return ChatMessage(role=role, content=content)

    def _translate_error(self, exc: Exception) -> LLMError:
        """Map SDK exceptions into local error types."""
        if isinstance(exc, LLMError):
            return exc
        if isinstance(exc, OpenAIRateLimitError):
            retry_after: float | None = None
            response = getattr(exc, "response", None)
            headers = getattr(response, "headers", None)
            if headers is not None:
                retry_after_value = headers.get("retry-after")
                if retry_after_value:
                    try:
                        retry_after = float(retry_after_value)
                    except ValueError:
                        retry_after = None
            return RateLimitError(str(exc), retry_after=retry_after)
        if isinstance(exc, (APIConnectionError, APITimeoutError)):
            return NetworkError(str(exc))
        if isinstance(exc, BadRequestError):
            return InvalidResponseError(str(exc))
        if isinstance(exc, APIStatusError):
            if exc.status_code >= 500:
                return NetworkError(str(exc))
            return InvalidResponseError(str(exc))
        return InvalidResponseError(str(exc))
