"""CapabilityInvoker — runtime dispatch from id → actual execution.

Looks up an id in the RegistryHub, identifies its tier (atom / composite /
agent / preset), and delegates to the right adapter:

    composite (CapabilityContract)  → bound Python function (Batch D map)
    atom      (ToolContract)         → adapter (Batch F: local / http / mcp / a2a)
    agent     (AgentContract)        → recursive sub-workflow (Batch X)

For Batch D MVP everything that is not a hardcoded native binding falls back
to a deterministic ``MockExecutor`` so the engine + UI + tracing path can be
exercised end to end without external services. Real adapters land in F.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.core.trace.bus import emit
from app.core.transport import (
    TransportAdapter,
    TransportError,
    get_transport_registry,
)
from app.ontology import AgentContract
from app.registry.registry_hub import EntryKind, RegistryEntry, get_entry

_LOG = logging.getLogger("agent_ops.workflow_engine.invoker")

# Type alias for native bindings (sync or async, returning a dict)
NativeBinding = Callable[[dict[str, Any]], Awaitable[dict[str, Any]] | dict[str, Any]]


class InvocationResult(BaseModel):
    """Outcome of one capability call."""

    capability_id: str
    kind: EntryKind
    output: dict[str, Any] = Field(default_factory=dict)
    used_mock: bool = False
    error: str | None = None
    elapsed_ms: int = 0

    model_config = ConfigDict(extra="forbid")


class CapabilityInvoker:
    """Resolve a capability_id to a runnable and execute it."""

    def __init__(
        self,
        bindings: dict[str, NativeBinding] | None = None,
        *,
        mock_when_missing: bool = True,
        transport_registry: object | None = None,
    ) -> None:
        self._bindings: dict[str, NativeBinding] = dict(bindings or {})
        self._mock = mock_when_missing
        # Lazily resolve the default registry so tests that override it via
        # reset_transport_registry() pick up their custom adapters.
        self._transport_registry = transport_registry

    def register(self, capability_id: str, fn: NativeBinding) -> None:
        """Add or replace a native binding."""
        self._bindings[capability_id] = fn

    async def invoke(
        self,
        capability_id: str,
        inputs: dict[str, Any],
        *,
        kind_hint: EntryKind | None = None,
    ) -> InvocationResult:
        """Run the capability and return its output dict."""
        loop = asyncio.get_event_loop()
        start = loop.time()

        # 1) Look up the entry across the registry pyramid
        entry = self._lookup(capability_id, kind_hint)
        if entry is None:
            elapsed = int((loop.time() - start) * 1000)
            return InvocationResult(
                capability_id=capability_id,
                kind=kind_hint or "composite",
                output={},
                used_mock=False,
                error=f"capability '{capability_id}' not in registry",
                elapsed_ms=elapsed,
            )

        emit(
            "L3", "CapabilityInvoker", "invoke_start",
            f"id={entry.id} · kind={entry.kind}",
            data={"inputs_keys": list(inputs.keys())},
        )

        # 2) Native binding wins; otherwise route by transport adapter for
        #    AgentContracts whose transport_type is non-local; mock as last resort.
        binding = self._bindings.get(capability_id)
        try:
            if binding is not None:
                output = await _run_binding(binding, inputs)
                used_mock = False
            elif _should_use_transport(entry):
                output = await self._invoke_via_transport(entry, inputs)
                used_mock = False
            elif self._mock:
                output = self._mock_output(entry, inputs)
                used_mock = True
            else:
                raise RuntimeError(f"no binding for '{capability_id}' and mock disabled")
        except Exception as exc:  # noqa: BLE001
            elapsed = int((loop.time() - start) * 1000)
            emit(
                "L3", "CapabilityInvoker", "invoke_fail",
                f"id={entry.id} · {type(exc).__name__}: {exc}",
                data={"elapsed_ms": elapsed},
            )
            return InvocationResult(
                capability_id=entry.id,
                kind=entry.kind,
                output={},
                used_mock=False,
                error=f"{type(exc).__name__}: {exc}",
                elapsed_ms=elapsed,
            )

        elapsed = int((loop.time() - start) * 1000)
        emit(
            "L3", "CapabilityInvoker", "invoke_done",
            f"id={entry.id} · used_mock={used_mock} · keys={list(output.keys())}",
            data={"elapsed_ms": elapsed, "used_mock": used_mock},
        )
        return InvocationResult(
            capability_id=entry.id,
            kind=entry.kind,
            output=output if isinstance(output, dict) else {"value": output},
            used_mock=used_mock,
            error=None,
            elapsed_ms=elapsed,
        )

    # ---- Internal ----
    def _lookup(self, capability_id: str, kind_hint: EntryKind | None) -> RegistryEntry | None:
        if kind_hint is not None:
            entry = get_entry(kind_hint, capability_id)
            if entry is not None:
                return entry
        # Try every tier
        for k in ("composite", "atom", "agent", "preset"):
            entry = get_entry(k, capability_id)  # type: ignore[arg-type]
            if entry is not None:
                return entry
        return None

    async def _invoke_via_transport(
        self, entry: RegistryEntry, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        """Resolve the AgentContract from the registry entry and dispatch to the adapter."""
        registry = self._transport_registry or get_transport_registry()
        contract = AgentContract.model_validate(entry.raw)
        adapter: TransportAdapter | None = registry.get(contract.transport_type)
        if adapter is None:
            raise TransportError(
                f"no transport adapter registered for type={contract.transport_type!r}"
            )
        timeout = float(contract.latency_budget_ms or 60_000) / 1000.0
        emit(
            "L3", "Transport", "dispatch",
            f"id={contract.agent_id} · transport={contract.transport_type} · timeout={timeout}s",
        )
        return await adapter.invoke(contract, inputs, timeout=timeout)

    def _mock_output(self, entry: RegistryEntry, inputs: dict[str, Any]) -> dict[str, Any]:
        """Synthesize an output that satisfies the declared output_schema (best-effort)."""
        raw = entry.raw or {}
        output_schema = raw.get("output_schema") or {}
        properties = output_schema.get("properties") if isinstance(output_schema, dict) else None
        if isinstance(properties, dict) and properties:
            out: dict[str, Any] = {}
            for key, spec in properties.items():
                out[key] = _mock_value(spec, key)
            out["__mock__"] = True
            return out
        # No schema — return a generic mock envelope
        return {
            "__mock__": True,
            "capability": entry.id,
            "echo_inputs": inputs,
            "note": f"deterministic mock output (no native binding for {entry.id})",
        }


def _should_use_transport(entry: RegistryEntry) -> bool:
    """An agent-tier entry whose transport is non-local goes through an adapter."""
    if entry.kind != "agent":
        return False
    raw = entry.raw or {}
    tt = raw.get("transport_type", "local")
    return tt in ("http", "mcp", "a2a", "cli")


async def _run_binding(binding: NativeBinding, inputs: dict[str, Any]) -> dict[str, Any]:
    res = binding(inputs)
    if asyncio.iscoroutine(res):
        res = await res
    if not isinstance(res, dict):
        return {"value": res}
    return res


def _mock_value(spec: object, key: str) -> Any:
    """Produce a deterministic placeholder consistent with a (loose) JSON Schema."""
    if not isinstance(spec, dict):
        return f"<mock:{key}>"
    t = spec.get("type")
    if t == "string":
        return f"<mock:{key}>"
    if t == "integer":
        return 0
    if t == "number":
        return 0.0
    if t == "boolean":
        return False
    if t == "array":
        return []
    if t == "object":
        return {}
    return None
