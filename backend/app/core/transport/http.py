"""HttpAdapter — JSON POST to an AgentContract.endpoint (Batch F).

Usage contract:

    POST {AgentContract.endpoint}
    Content-Type: application/json
    Authorization: Bearer ...   (when auth_mode='bearer')
    Body: {{ inputs JSON }}

    → 200 with JSON body  → returned as dict
    → 401/403             → TransportAuthError (no retry)
    → 5xx                 → TransportRemoteError (engine may retry)
    → timeout             → TransportTimeoutError
    → anything else 4xx   → TransportError

Bearer token resolution order:
    1. ``contract.runtime_config['bearer_token']`` (literal value)
    2. ``contract.runtime_config['bearer_token_env']`` → os.environ lookup

Never log the resolved token.
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.core.transport.base import (
    TransportAdapter,
    TransportAuthError,
    TransportError,
    TransportRemoteError,
    TransportTimeoutError,
)
from app.ontology import AgentContract


class HttpAdapter(TransportAdapter):
    """Generic JSON-over-HTTP adapter."""

    transport_type = "http"

    def __init__(
        self,
        *,
        default_timeout: float = 60.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._default_timeout = default_timeout
        if http_client is not None:
            self._client = http_client
            self._owns_client = False
        else:
            self._client = httpx.AsyncClient(timeout=default_timeout)
            self._owns_client = True

    async def invoke(
        self,
        contract: AgentContract,
        inputs: dict[str, Any],
        *,
        timeout: float = 60.0,
    ) -> dict[str, Any]:
        endpoint = contract.endpoint
        if not endpoint:
            raise TransportError(
                f"agent {contract.agent_id!r} has no endpoint for http transport"
            )

        headers = {"Content-Type": "application/json"}
        headers.update(_auth_headers(contract))

        try:
            response = await self._client.post(
                endpoint,
                json=inputs,
                headers=headers,
                timeout=timeout,
            )
        except httpx.TimeoutException as exc:
            raise TransportTimeoutError(
                f"http call to {endpoint} timed out after {timeout}s"
            ) from exc
        except httpx.HTTPError as exc:
            raise TransportError(
                f"http call to {endpoint} failed: {type(exc).__name__}: {exc}"
            ) from exc

        if response.status_code in (401, 403):
            raise TransportAuthError(
                f"auth rejected by {endpoint} (status={response.status_code})"
            )
        if 500 <= response.status_code < 600:
            raise TransportRemoteError(
                f"remote error from {endpoint}: status={response.status_code}"
                f" body={_truncate(response.text)}"
            )
        if response.status_code >= 400:
            raise TransportError(
                f"http error from {endpoint}: status={response.status_code}"
                f" body={_truncate(response.text)}"
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise TransportError(
                f"non-JSON response from {endpoint}: {_truncate(response.text)}"
            ) from exc

        if not isinstance(data, dict):
            return {"value": data}
        return data

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _auth_headers(contract: AgentContract) -> dict[str, str]:
    mode = contract.auth_mode
    cfg = contract.runtime_config or {}
    if mode == "none":
        return {}
    if mode == "bearer":
        token = cfg.get("bearer_token")
        if not token:
            env_key = cfg.get("bearer_token_env")
            if env_key:
                token = os.environ.get(str(env_key))
        if not token:
            raise TransportAuthError(
                f"agent {contract.agent_id!r} declares auth_mode=bearer but "
                f"no bearer_token / bearer_token_env in runtime_config"
            )
        return {"Authorization": f"Bearer {token}"}
    # mtls / oauth / custom — must be wired by site-specific subclass
    raise TransportAuthError(
        f"unsupported auth_mode={mode!r} for default HttpAdapter "
        f"(extend HttpAdapter to support it)"
    )


def _truncate(text: str, n: int = 240) -> str:
    if len(text) <= n:
        return text
    return text[:n] + "…"


__all__ = ["HttpAdapter"]
