"""Transport adapters — cross-boundary agent invocation (Batch F).

Up through Batch Z++ every "agent" was an in-process Python binding registered
on :class:`CapabilityInvoker`. Real interoperability requires us to also reach
agents that live behind a network boundary (HTTP service), an MCP server, or
another A2A-speaking system.

This package lays down the abstraction:

    TransportAdapter (abstract) — invoke(contract, inputs) -> output dict
        ├── LocalAdapter   — passes through to a registered native binding
        ├── HttpAdapter    — JSON POST to AgentContract.endpoint
        ├── McpAdapter     — TODO stub (Batch F-2)
        └── A2aAdapter     — TODO stub (Batch F-2)

The default :func:`get_transport_registry` returns a registry pre-populated
with Local + HTTP. Workflow engine fetches adapters from it whenever an
``AgentContract.transport_type`` indicates remote dispatch.
"""
from __future__ import annotations

from app.core.transport.base import (
    TransportAdapter,
    TransportAuthError,
    TransportError,
    TransportRemoteError,
    TransportTimeoutError,
)
from app.core.transport.http import HttpAdapter
from app.core.transport.local import LocalAdapter
from app.core.transport.registry import (
    TransportRegistry,
    get_transport_registry,
    reset_transport_registry,
)

__all__ = [
    "TransportAdapter",
    "TransportError",
    "TransportAuthError",
    "TransportTimeoutError",
    "TransportRemoteError",
    "LocalAdapter",
    "HttpAdapter",
    "TransportRegistry",
    "get_transport_registry",
    "reset_transport_registry",
]
