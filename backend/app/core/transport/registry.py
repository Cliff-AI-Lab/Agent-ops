"""TransportRegistry — singleton lookup of adapter by transport_type."""
from __future__ import annotations

from app.core.transport.base import TransportAdapter
from app.core.transport.http import HttpAdapter
from app.core.transport.local import LocalAdapter


class TransportRegistry:
    """Maps ``transport_type`` (e.g. 'http') to an adapter instance."""

    def __init__(self) -> None:
        self._adapters: dict[str, TransportAdapter] = {}

    def register(self, adapter: TransportAdapter) -> None:
        """Add or replace an adapter keyed by its ``transport_type``."""
        if not adapter.transport_type:
            raise ValueError("TransportAdapter.transport_type must be non-empty")
        self._adapters[adapter.transport_type] = adapter

    def get(self, transport_type: str) -> TransportAdapter | None:
        return self._adapters.get(transport_type)

    def has(self, transport_type: str) -> bool:
        return transport_type in self._adapters

    def types(self) -> list[str]:
        return sorted(self._adapters.keys())

    async def aclose_all(self) -> None:
        for adapter in self._adapters.values():
            await adapter.aclose()


_registry: TransportRegistry | None = None


def get_transport_registry() -> TransportRegistry:
    """Return the process-wide registry, creating it lazily on first use.

    The default registry contains:

      - ``LocalAdapter`` (no bindings; ``register()`` to attach handlers)
      - ``HttpAdapter`` (default timeout 60s)

    MCP / A2A / CLI adapters are not registered by default — Batch F-2 will
    add them. Tests should call :func:`reset_transport_registry` to start
    from a clean slate.
    """
    global _registry
    if _registry is None:
        _registry = TransportRegistry()
        _registry.register(LocalAdapter())
        _registry.register(HttpAdapter())
    return _registry


def reset_transport_registry() -> None:
    """Test helper: drop the singleton so the next ``get_*`` rebuilds it."""
    global _registry
    _registry = None


__all__ = [
    "TransportRegistry",
    "get_transport_registry",
    "reset_transport_registry",
]
