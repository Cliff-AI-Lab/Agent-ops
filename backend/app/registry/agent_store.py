"""AgentStore — third-tier registry of AgentContracts (Batch Y).

Layout::

    agents/
        web_research/
            agent.yaml   ← AgentContract document
            README.md    ← optional curated MD (preferred over auto-generated)
        report_summarizer/
            agent.yaml
            README.md
        customer_service/
            agent.yaml
            README.md

Design parallels :mod:`app.registry.tool_store` / :mod:`app.registry.preset_store`,
keyed by ``agent_id``.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path

import yaml

from app.ontology import AgentContract

_LOG = logging.getLogger("agent_ops.registry.agents")


def load_agent_from_dir(agent_dir: Path) -> AgentContract:
    """Load ``agent_dir/agent.yaml`` and validate it as an AgentContract."""
    yaml_path = agent_dir / "agent.yaml"
    if not yaml_path.is_file():
        raise FileNotFoundError(f"missing agent.yaml in {agent_dir}")
    with yaml_path.open(encoding="utf-8") as fp:
        data = yaml.safe_load(fp) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{yaml_path}: top-level YAML must be a mapping")
    return AgentContract.model_validate(data)


def load_agents_from_dir(agents_root: Path) -> list[tuple[AgentContract, Path]]:
    """Each child directory of ``agents_root`` containing an ``agent.yaml`` is one agent."""
    if not agents_root.is_dir():
        _LOG.warning("agents dir not found: %s", agents_root)
        return []
    out: list[tuple[AgentContract, Path]] = []
    for child in sorted(agents_root.iterdir()):
        if not child.is_dir():
            continue
        if not (child / "agent.yaml").is_file():
            continue
        try:
            out.append((load_agent_from_dir(child), child))
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("failed to load agent %s: %s", child, exc)
    return out


class AgentStore:
    """In-memory registry of AgentContract (keyed by agent_id + version)."""

    def __init__(self) -> None:
        self._by_id: dict[str, dict[str, AgentContract]] = {}
        self._dirs: dict[str, Path] = {}

    def register(self, agent: AgentContract, source_dir: Path | None = None) -> None:
        self._by_id.setdefault(agent.agent_id, {})[agent.version] = agent
        if source_dir is not None:
            self._dirs[agent.agent_id] = source_dir

    def register_many(self, pairs: Iterable[tuple[AgentContract, Path]]) -> int:
        n = 0
        for a, d in pairs:
            self.register(a, d)
            n += 1
        return n

    def get(self, agent_id: str, version: str | None = None) -> AgentContract | None:
        versions = self._by_id.get(agent_id) or {}
        if version is not None:
            return versions.get(version)
        if not versions:
            return None
        active = [a for a in versions.values() if a.activation_status == "active"]
        pool = active or list(versions.values())
        return max(pool, key=lambda a: a.version)

    def source_dir(self, agent_id: str) -> Path | None:
        return self._dirs.get(agent_id)

    def list(
        self,
        activation_status: str | None = None,
        tag: str | None = None,
    ) -> list[AgentContract]:
        flat: list[AgentContract] = []
        for versions in self._by_id.values():
            for a in versions.values():
                if activation_status and a.activation_status != activation_status:
                    continue
                if tag and tag not in a.tags:
                    continue
                flat.append(a)
        return sorted(flat, key=lambda a: a.agent_id)

    def __len__(self) -> int:
        return sum(len(v) for v in self._by_id.values())


_agent_store: AgentStore | None = None


def get_agent_store() -> AgentStore:
    global _agent_store
    if _agent_store is None:
        _agent_store = AgentStore()
    return _agent_store


def reset_agent_store() -> None:
    global _agent_store
    _agent_store = AgentStore()


def default_agents_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "agents"


def bootstrap_agents(agents_dir: Path | None = None) -> int:
    root = agents_dir or default_agents_dir()
    pairs = load_agents_from_dir(root)
    n = get_agent_store().register_many(pairs)
    _LOG.info("registered %d agents from %s", n, root)
    return n
