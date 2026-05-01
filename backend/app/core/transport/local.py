"""LocalAdapter — pass-through to an in-process native binding (Batch F)."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from app.core.transport.base import TransportAdapter, TransportError
from app.ontology import AgentContract

NativeBinding = Callable[[dict[str, Any]], Awaitable[dict[str, Any]] | dict[str, Any]]


class LocalAdapter(TransportAdapter):
    """In-process adapter: looks up a binding by ``agent_id`` and runs it.

    Bindings can be sync or async; both branches end up returning a dict.
    Useful so ``CapabilityInvoker`` can have a uniform "go through an adapter"
    code path even for the local case.
    """

    transport_type = "local"

    def __init__(self, bindings: dict[str, NativeBinding] | None = None) -> None:
        self._bindings: dict[str, NativeBinding] = dict(bindings or {})

    def register(self, agent_id: str, fn: NativeBinding) -> None:
        """Add or replace a local binding."""
        self._bindings[agent_id] = fn

    def has(self, agent_id: str) -> bool:
        return agent_id in self._bindings

    async def invoke(
        self,
        contract: AgentContract,
        inputs: dict[str, Any],
        *,
        timeout: float = 60.0,
    ) -> dict[str, Any]:
        binding = self._bindings.get(contract.agent_id)
        if binding is None:
            raise TransportError(
                f"no local binding registered for agent_id={contract.agent_id!r}"
            )
        try:
            res = binding(inputs)
            if asyncio.iscoroutine(res):
                res = await asyncio.wait_for(res, timeout=timeout)
        except asyncio.TimeoutError as exc:
            from app.core.transport.base import TransportTimeoutError
            raise TransportTimeoutError(
                f"local binding {contract.agent_id} timed out after {timeout}s"
            ) from exc
        if not isinstance(res, dict):
            return {"value": res}
        return res


__all__ = ["LocalAdapter", "NativeBinding"]
