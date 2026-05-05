"""SoftWarnBudget - V2.3 wrapper that adds 80% soft-warning to any CostBudget.

Wraps an underlying CostBudget. Approval logic unchanged (delegates), but
when approved AND estimate >= warn_threshold * underlying.threshold, emits
L5 trace event 'budget_soft_warn' so dashboards / alerting can flag near-cap
spend without blocking the build.

Composable: works with ThresholdCostBudget, RollingBudget, or any future impl.
"""
from __future__ import annotations

from typing import Any

from app.core.governance.interface import CostBudget, CostBudgetDecision, CostEstimate
from app.core.trace.bus import emit


class SoftWarnBudget:
    """Decorator/wrapper adding soft-warning at warn_ratio * threshold."""

    def __init__(
        self,
        underlying: CostBudget,
        warn_ratio: float = 0.80,
    ) -> None:
        if not (0.0 < warn_ratio < 1.0):
            raise ValueError(
                f"warn_ratio must be in (0, 1) exclusive; got {warn_ratio}"
            )
        self._underlying = underlying
        self._warn_ratio = warn_ratio

    @property
    def threshold_cny(self) -> float:
        return self._underlying.threshold_cny

    @property
    def warn_threshold_cny(self) -> float:
        return self._underlying.threshold_cny * self._warn_ratio

    def _maybe_warn(self, decision: CostBudgetDecision) -> None:
        if not decision.approved:
            return
        warn_at = self.warn_threshold_cny
        est = decision.estimate.estimated_total_cny
        if est >= warn_at:
            emit(
                "L5",
                "SoftWarnBudget",
                "budget_soft_warn",
                f"estimate={est:.4f} CNY at {est / decision.threshold_cny:.0%} "
                f"of threshold {decision.threshold_cny:.4f} CNY",
                data={
                    "estimate_cny": est,
                    "threshold_cny": decision.threshold_cny,
                    "warn_threshold_cny": warn_at,
                    "ratio": self._warn_ratio,
                },
            )

    def check(self, estimate: CostEstimate) -> CostBudgetDecision:
        decision = self._underlying.check(estimate)
        self._maybe_warn(decision)
        return decision

    async def check_async(self, estimate: CostEstimate) -> CostBudgetDecision:
        check_async: Any = getattr(self._underlying, "check_async", None)
        if check_async is not None:
            decision = await check_async(estimate)
        else:
            decision = self._underlying.check(estimate)
        self._maybe_warn(decision)
        return decision
