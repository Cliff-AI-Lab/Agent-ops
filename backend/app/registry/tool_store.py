"""ToolStore + ToolContract YAML loader for the Tool Wiki (Batch C+)."""
from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml

from app.ontology import ToolContract

_LOG = logging.getLogger("agent_ops.registry.tools")


def load_tool_from_yaml(path: Path) -> ToolContract:
    with path.open(encoding="utf-8") as fp:
        data = yaml.safe_load(fp) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path.name}: top-level YAML must be a mapping")
    return ToolContract.model_validate(data)


def load_tools_from_dir(dir_path: Path) -> list[ToolContract]:
    """Recursively load every ``*.yaml`` under ``dir_path`` as a ToolContract."""
    if not dir_path.is_dir():
        _LOG.warning("tools dir not found: %s", dir_path)
        return []
    tools: list[ToolContract] = []
    for yaml_path in sorted(dir_path.rglob("*.yaml")):
        try:
            tools.append(load_tool_from_yaml(yaml_path))
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("failed to load tool %s: %s", yaml_path, exc)
    return tools


class ToolStore:
    """In-memory registry of ToolContract (keyed by tool_id + version)."""

    def __init__(self) -> None:
        self._by_id: dict[str, dict[str, ToolContract]] = {}

    def register(self, tool: ToolContract) -> None:
        self._by_id.setdefault(tool.tool_id, {})[tool.version] = tool

    def register_many(self, tools: Iterable[ToolContract]) -> int:
        n = 0
        for t in tools:
            self.register(t)
            n += 1
        return n

    def get(self, tool_id: str, version: str | None = None) -> ToolContract | None:
        versions = self._by_id.get(tool_id) or {}
        if version is not None:
            return versions.get(version)
        if not versions:
            return None
        active = [t for t in versions.values() if t.activation_status == "active"]
        pool = active or list(versions.values())
        return max(pool, key=lambda t: t.version)

    def list(
        self,
        activation_status: str | None = None,
        category: str | None = None,
        provider: str | None = None,
        tag: str | None = None,
    ) -> list[ToolContract]:
        flat: list[ToolContract] = []
        for versions in self._by_id.values():
            for t in versions.values():
                if activation_status and t.activation_status != activation_status:
                    continue
                if category and t.category != category:
                    continue
                if provider and t.provider.lower() != provider.lower():
                    continue
                if tag and tag not in t.tags:
                    continue
                flat.append(t)
        return sorted(flat, key=lambda t: (t.category, t.tool_id))

    def search(self, query: str) -> list[ToolContract]:
        """Simple case-insensitive substring search across id / name / description / tags."""
        q = (query or "").strip().lower()
        if not q:
            return self.list()
        hits: list[ToolContract] = []
        for versions in self._by_id.values():
            for t in versions.values():
                hay = " ".join([
                    t.tool_id, t.name, t.description, t.provider,
                    t.category, " ".join(t.tags),
                ]).lower()
                if q in hay:
                    hits.append(t)
        return sorted(hits, key=lambda t: (t.category, t.tool_id))

    def categories(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for versions in self._by_id.values():
            for t in versions.values():
                counts[t.category] = counts.get(t.category, 0) + 1
        return counts

    def providers(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for versions in self._by_id.values():
            for t in versions.values():
                counts[t.provider] = counts.get(t.provider, 0) + 1
        return counts

    def __len__(self) -> int:
        return sum(len(v) for v in self._by_id.values())


_tool_store: ToolStore | None = None


def get_tool_store() -> ToolStore:
    global _tool_store
    if _tool_store is None:
        _tool_store = ToolStore()
    return _tool_store


def reset_tool_store() -> None:
    global _tool_store
    _tool_store = ToolStore()


def default_tools_dir() -> Path:
    """``tools/`` at project root (sibling of ``capabilities/``)."""
    return Path(__file__).resolve().parents[3] / "tools"


def bootstrap_tools(tools_dir: Path | None = None) -> int:
    root = tools_dir or default_tools_dir()
    tools = load_tools_from_dir(root)
    registered = get_tool_store().register_many(tools)
    _LOG.info("registered %d tools from %s", registered, root)
    return registered
