"""Abstract TransportAdapter + error hierarchy (Batch F)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from app.ontology import AgentContract


class TransportError(Exception):
    """Base class for transport-layer failures (network, protocol, decode)."""


class TransportAuthError(TransportError):
    """401/403 or missing credentials. Should NOT be retried by the engine."""


class TransportTimeoutError(TransportError):
    """Connection or read timed out. Engine may retry per WorkflowStep policy."""


class TransportRemoteError(TransportError):
    """5xx / remote-side failure. Engine may retry per policy."""


class TransportAdapter(ABC):
    """Pluggable transport for an :class:`AgentContract`.

    Subclasses MUST set ``transport_type`` (matching the AgentContract field)
    and implement :meth:`invoke`. They may optionally override :meth:`aclose`
    to release sockets / sub-process handles.
    """

    transport_type: ClassVar[str] = ""

    @abstractmethod
    async def invoke(
        self,
        contract: AgentContract,
        inputs: dict[str, Any],
        *,
        timeout: float = 60.0,
    ) -> dict[str, Any]:
        """Run the contract with the given inputs and return its output dict.

        Implementations must raise a subclass of :class:`TransportError` on
        any non-success path so the engine can map to its retry/failure policy.
        """

    async def aclose(self) -> None:  # pragma: no cover - default no-op
        """Release any held resources. Called from process shutdown."""
        return None


__all__ = [
    "TransportAdapter",
    "TransportError",
    "TransportAuthError",
    "TransportTimeoutError",
    "TransportRemoteError",
]
