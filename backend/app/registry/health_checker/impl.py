"""HealthChecker implementation. Phase 2."""

from __future__ import annotations

from typing import Literal


class HealthCheckerImpl:
    def __init__(self, atom_loader=None) -> None:
        self._atoms = atom_loader

    async def check_one(self, asset_id: str) -> Literal["green", "yellow", "red"]:
        raise NotImplementedError("HealthCheckerImpl: Phase 2.")

    async def check_all(self) -> dict[str, str]:
        raise NotImplementedError("HealthCheckerImpl: Phase 2.")
