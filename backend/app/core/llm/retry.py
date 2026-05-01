from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

from app.core.llm.errors import NetworkError, RateLimitError

T = TypeVar("T")


async def with_retry(
    fn: Callable[[], Awaitable[T]],
    *,
    max_retries: int,
    base_delay: float,
    max_delay: float,
) -> T:
    """Run an async function with retry for transient LLM failures."""
    attempt = 0
    while True:
        try:
            return await fn()
        except (RateLimitError, NetworkError):
            if attempt >= max_retries:
                raise
            delay = min(max_delay, base_delay * (2**attempt))
            jittered_delay = delay * random.uniform(0.9, 1.1)
            await asyncio.sleep(jittered_delay)
            attempt += 1
