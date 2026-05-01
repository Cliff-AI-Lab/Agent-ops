"""Agent Ops Capability Registry (blueprint M2).

Holds the authoritative, versioned list of CapabilityContracts available to
the workflow engine. Loaded from YAML under ``capabilities/`` at startup.
"""
from __future__ import annotations

from app.registry.bootstrap import bootstrap_registry
from app.registry.loader import (
    load_capabilities_from_dir,
    load_capability_from_yaml,
    resolve_schema_ref,
)
from app.registry.store import CapabilityStore, get_store, reset_store
from app.registry.tool_store import (
    ToolStore,
    bootstrap_tools,
    default_tools_dir,
    get_tool_store,
    load_tool_from_yaml,
    load_tools_from_dir,
    reset_tool_store,
)

__all__ = [
    "CapabilityStore",
    "bootstrap_registry",
    "get_store",
    "load_capabilities_from_dir",
    "load_capability_from_yaml",
    "reset_store",
    "resolve_schema_ref",
    "ToolStore",
    "bootstrap_tools",
    "default_tools_dir",
    "get_tool_store",
    "load_tool_from_yaml",
    "load_tools_from_dir",
    "reset_tool_store",
]
