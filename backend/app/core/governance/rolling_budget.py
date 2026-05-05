"""RollingBudget - V2.2 W2 daily/monthly cap budgets backed by CostLedger.

Per-build check uses CURRENT spent + this estimate <= cap. Refuses if either
daily or monthly cap would be exceeded by approving this build.

V2.3: per-tenant allowance overrides + soft 80% warning hooks.
"""
from __future__ import annotations

from typing import Optional

from app.core.governance.interface import CostBudgetDecision, CostEstimate
from app.core.governance.ledger import CostLedger
from app.core.trace.bus import emit


class RollingBudget:
    """Daily + monthly rolling cap, backed by CostLedger.

    Either cap may be None (= unlimited that dimension); both must be set
    to use this gate fully. threshold_cny is the daily cap (matches
    CostBudget Protocol attribute).
    """

    def __init__(
        self,
        ledger: CostLedger,
        daily_cap_cny: Optional[float] = None,
        monthly_cap_cny: Optional[float] = None,
        tenant_id: str = "default",
    ) -> None:
        if daily_cap_cny is None and monthly_cap_cny is None:
            raise ValueError(
                "at least one of daily_cap_cny / monthly_cap_cny must be set"
            )
        if daily_cap_cny is not None and daily_cap_cny <= 0:
            raise ValueError(f"daily_cap_cny must be positive; got {daily_cap_cny}")
        if monthly_cap_cny is not None and monthly_cap_cny <= 0:
            raise ValueError(f"monthly_cap_cny must be positive; got {monthly_cap_cny}")

        self._ledger = ledger
        self._daily = daily_cap_cny
        self._monthly = monthly_cap_cny
        self._tenant = tenant_id

    @property
    def threshold_cny(self) -> float:
        """For Protocol compatibility — the more constraining cap."""
        if self._daily is not None and self._monthly is not None:
            return min(self._daily, self._monthly)
        return self._daily if self._daily is not None else self._monthly  # type: ignore[return-value]

    async def check_async(self, estimate: CostEstimate) -> CostBudgetDecision:
        """Async check (since we hit the DB). Use this in async pipelines.

        Sync .check() falls back to async via asyncio.run — only safe when
        not already in an event loop. Prefer check_async in production.
        """
        spent_today = 0.0
        spent_month = 0.0
        if self._daily is not None:
            spent_today = await self._ledger.daily_spent(tenant_id=self._tenant)
        if self._monthly is not None:
            spent_month = await self._ledger.monthly_spent(tenant_id=self._tenant)

        approved = True
        reasons: list[str] = []

        if self._daily is not None:
            after_today = spent_today + estimate.estimated_total_cny
            if after_today > self._daily:
                approved = False
                reasons.append(
                    f"daily cap exceeded: spent {spent_today:.4f} + this "
                    f"{estimate.estimated_total_cny:.4f} = {after_today:.4f} > "
                    f"cap {self._daily:.4f} CNY"
                )

        if self._monthly is not None:
            after_month = spent_month + estimate.estimated_total_cny
            if after_month > self._monthly:
                approved = False
                reasons.append(
                    f"monthly cap exceeded: spent {spent_month:.4f} + this "
                    f"{estimate.estimated_total_cny:.4f} = {after_month:.4f} > "
                    f"cap {self._monthly:.4f} CNY"
                )

        if approved:
            reason = (
                f"approved · today {spent_today:.4f}+{estimate.estimated_total_cny:.4f}"
                + (f" / cap {self._daily:.4f}" if self._daily else "")
                + (
                    f" · month {spent_month:.4f} / cap {self._monthly:.4f}"
                    if self._monthly
                    else ""
                )
            )
        else:
            reason = " ; ".join(reasons)

        emit(
            "L5",
            "RollingBudget",
            "rolling_budget_check",
            f"approved={approved} tenant={self._tenant} "
            f"day_spent={spent_today:.4f} month_spent={spent_month:.4f} "
            f"this={estimate.estimated_total_cny:.4f}",
            data={
                "approved": approved,
                "tenant_id": self._tenant,
                "spent_today_cny": spent_today,
                "spent_month_cny": spent_month,
                "estimate_cny": estimate.estimated_total_cny,
                "daily_cap_cny": self._daily,
                "monthly_cap_cny": self._monthly,
            },
        )

        return CostBudgetDecision(
            approved=approved,
            estimate=estimate,
            threshold_cny=self.threshold_cny,
            reason=reason,
        )

    def check(self, estimate: CostEstimate) -> CostBudgetDecision:
        """Sync wrapper for tests / non-async callers."""
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            raise RuntimeError(
                "RollingBudget.check() called inside an event loop; "
                "use check_async() instead"
            )
        return asyncio.run(self.check_async(estimate))
