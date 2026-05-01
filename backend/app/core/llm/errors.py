from __future__ import annotations


class LLMError(Exception):
    """Base exception for LLM operations."""


class RateLimitError(LLMError):
    """Raised when the provider returns a rate limit response."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class NetworkError(LLMError):
    """Raised for retriable network or upstream server failures."""


class InvalidResponseError(LLMError):
    """Raised for non-retriable API or payload errors."""


class ConfigError(LLMError):
    """Raised for invalid local configuration."""
