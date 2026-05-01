"""HealthChecker Protocol."""

from __future__ import annotations

from typing import Literal, Protocol


class HealthChecker(Protocol):
    """Run atom fixtures, update atom.health_check.status accordingly."""

    async def check_one(self, asset_id: str) -> Literal["green", "yellow", "red"]: ...
    async def check_all(self) -> dict[str, str]: ...
