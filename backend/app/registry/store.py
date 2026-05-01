"""In-memory CapabilityStore with versioning + diff + activation filtering."""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.ontology import CapabilityContract


class CapabilityStore:
    """Capability registry keyed by ``capability_id → {version → CapabilityContract}``."""

    def __init__(self) -> None:
        self._by_id: dict[str, dict[str, CapabilityContract]] = {}

    def register(self, contract: CapabilityContract) -> None:
        """Register or replace one contract (by capability_id + version)."""
        self._by_id.setdefault(contract.capability_id, {})[contract.version] = contract

    def register_many(self, contracts: Iterable[CapabilityContract]) -> int:
        n = 0
        for c in contracts:
            self.register(c)
            n += 1
        return n

    def get(self, capability_id: str, version: str | None = None) -> CapabilityContract | None:
        versions = self._by_id.get(capability_id) or {}
        if version is not None:
            return versions.get(version)
        if not versions:
            return None
        active = [c for c in versions.values() if c.activation_status == "active"]
        pool = active or list(versions.values())
        return max(pool, key=lambda c: c.version)

    def list(self, activation_status: str | None = None) -> list[CapabilityContract]:
        flat: list[CapabilityContract] = []
        for versions in self._by_id.values():
            for c in versions.values():
                if activation_status is None or c.activation_status == activation_status:
                    flat.append(c)
        return sorted(flat, key=lambda c: (c.capability_id, c.version))

    def list_ids(self) -> list[str]:
        return sorted(self._by_id.keys())

    def versions(self, capability_id: str) -> list[str]:
        return sorted((self._by_id.get(capability_id) or {}).keys())

    def diff(self, capability_id: str, v1: str, v2: str) -> dict[str, dict[str, Any]]:
        """Field-level diff between two versions of a capability."""
        a = self.get(capability_id, v1)
        b = self.get(capability_id, v2)
        if a is None or b is None:
            return {}
        da, db = a.model_dump(), b.model_dump()
        out: dict[str, dict[str, Any]] = {}
        for key in set(da) | set(db):
            if da.get(key) != db.get(key):
                out[key] = {"v1": da.get(key), "v2": db.get(key)}
        return out

    def __len__(self) -> int:
        return sum(len(v) for v in self._by_id.values())


_store: CapabilityStore | None = None


def get_store() -> CapabilityStore:
    """Return the singleton process-wide CapabilityStore."""
    global _store
    if _store is None:
        _store = CapabilityStore()
    return _store


def reset_store() -> None:
    """Drop the singleton (used by tests to start from a clean state)."""
    global _store
    _store = CapabilityStore()
