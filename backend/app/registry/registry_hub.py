"""RegistryHub — unified facade over the three-tier ontology pyramid (Batch Y).

The hub gives IntentRouter / Wiki / Asset-Hub a single search surface across:

- atom    → ToolStore       (tool_contract.py)
- composite → CapabilityStore (store.py)
- agent   → AgentStore       (agent_store.py)
- preset  → PresetStore      (preset_store.py — auxiliary tier)

Every entry is normalized into a :class:`RegistryEntry` that exposes the same
fields regardless of which underlying store it came from. This is the
"unified shelf" the user described — atoms, composites, generated agents
all visible side-by-side.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.registry.agent_store import get_agent_store
from app.registry.preset_store import get_preset_store
from app.registry.store import get_store
from app.registry.tool_store import get_tool_store

EntryKind = Literal["atom", "composite", "agent", "preset"]


class RegistryEntry(BaseModel):
    """Normalized view of any registered ontology object."""

    kind: EntryKind
    id: str
    version: str
    name: str
    description: str
    intent_keywords: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    activation_status: str
    is_generated: bool = Field(default=False)
    raw: dict = Field(
        default_factory=dict,
        description="Full source contract dump (model_dump).",
    )

    model_config = ConfigDict(extra="forbid")


def _atom_entries() -> list[RegistryEntry]:
    out: list[RegistryEntry] = []
    for tool in get_tool_store().list():
        out.append(
            RegistryEntry(
                kind="atom",
                id=tool.tool_id,
                version=tool.version,
                name=tool.name,
                description=tool.description,
                intent_keywords=list(tool.intent_keywords),
                tags=list(tool.tags),
                activation_status=tool.activation_status,
                is_generated=False,
                raw=tool.model_dump(),
            )
        )
    return out


def _composite_entries() -> list[RegistryEntry]:
    out: list[RegistryEntry] = []
    for cap in get_store().list():
        out.append(
            RegistryEntry(
                kind="composite",
                id=cap.capability_id,
                version=cap.version,
                name=cap.name,
                description=cap.description,
                intent_keywords=list(cap.intent_keywords),
                tags=[],
                activation_status=cap.activation_status,
                is_generated=False,
                raw=cap.model_dump(),
            )
        )
    return out


def _agent_entries() -> list[RegistryEntry]:
    out: list[RegistryEntry] = []
    for agent in get_agent_store().list():
        out.append(
            RegistryEntry(
                kind="agent",
                id=agent.agent_id,
                version=agent.version,
                name=agent.name,
                description=agent.description,
                intent_keywords=list(agent.intent_keywords),
                tags=list(agent.tags),
                activation_status=agent.activation_status,
                is_generated=agent.is_generated,
                raw=agent.model_dump(),
            )
        )
    return out


def _preset_entries() -> list[RegistryEntry]:
    out: list[RegistryEntry] = []
    for bundle in get_preset_store().list():
        out.append(
            RegistryEntry(
                kind="preset",
                id=bundle.bundle_id,
                version=bundle.version,
                name=bundle.name,
                description=bundle.description,
                intent_keywords=[],
                tags=list(bundle.provides_features),
                activation_status=bundle.activation_status,
                is_generated=False,
                raw=bundle.model_dump(),
            )
        )
    return out


def list_all(
    kind: EntryKind | None = None,
    activation_status: str | None = None,
) -> list[RegistryEntry]:
    """Flat list of every registered entry across all tiers, optionally filtered."""
    entries: list[RegistryEntry] = []
    if kind in (None, "atom"):
        entries.extend(_atom_entries())
    if kind in (None, "composite"):
        entries.extend(_composite_entries())
    if kind in (None, "agent"):
        entries.extend(_agent_entries())
    if kind in (None, "preset"):
        entries.extend(_preset_entries())
    if activation_status:
        entries = [e for e in entries if e.activation_status == activation_status]
    return sorted(entries, key=lambda e: (e.kind, e.id))


def get_entry(kind: EntryKind, entry_id: str) -> RegistryEntry | None:
    for e in list_all(kind=kind):
        if e.id == entry_id:
            return e
    return None


def search(
    query: str,
    kind: EntryKind | None = None,
    activation_status: str | None = "active",
) -> list[RegistryEntry]:
    """Free-text search across id / name / description / tags / intent_keywords."""
    q = (query or "").strip().lower()
    if not q:
        return list_all(kind=kind, activation_status=activation_status)
    hits: list[RegistryEntry] = []
    for entry in list_all(kind=kind, activation_status=activation_status):
        haystack = " ".join([
            entry.id, entry.name, entry.description,
            " ".join(entry.tags), " ".join(entry.intent_keywords),
        ]).lower()
        if q in haystack:
            hits.append(entry)
            continue
        # Multi-word AND search
        if all(token in haystack for token in q.split() if token):
            hits.append(entry)
    return hits


def stats() -> dict[str, int]:
    """Per-tier counts (used by the unified Wiki summary)."""
    return {
        "atom": sum(1 for _ in _atom_entries()),
        "composite": sum(1 for _ in _composite_entries()),
        "agent": sum(1 for _ in _agent_entries()),
        "preset": sum(1 for _ in _preset_entries()),
    }
