"""ThresholdCostBudget - per-build hard cap (V2.2 W1).

V2.2 simple gate: if estimate.total > threshold, refuse.
V2.3+ extensions:
  - rolling daily / monthly budgets (DB-backed)
  - per-tenant allowance
  - soft warning at 80% before hard refuse
  - escalation hooks (notify finance Slack, etc.)
"""
from __future__ import annotations

from app.core.governance.interface import CostBudgetDecision, CostEstimate
from app.core.trace.bus import emit


class ThresholdCostBudget:
    """Single-shot gate: estimate.total <= threshold."""

    def __init__(self, threshold_cny: float = 1.00) -> None:
        if threshold_cny <= 0:
            raise ValueError(f"threshold must be positive CNY; got {threshold_cny}")
        self.threshold_cny = threshold_cny

    def check(self, estimate: CostEstimate) -> CostBudgetDecision:
        approved = estimate.estimated_total_cny <= self.threshold_cny
        if approved:
            reason = (
                f"estimate {estimate.estimated_total_cny:.4f} CNY <= "
                f"threshold {self.threshold_cny:.4f} CNY"
            )
        else:
            reason = (
                f"estimate {estimate.estimated_total_cny:.4f} CNY exceeds "
                f"threshold {self.threshold_cny:.4f} CNY by "
                f"{estimate.estimated_total_cny - self.threshold_cny:.4f} CNY"
            )

        emit(
            "L5",
            "ThresholdCostBudget",
            "budget_check",
            f"approved={approved} est={estimate.estimated_total_cny:.4f} "
            f"limit={self.threshold_cny:.4f}",
            data={
                "approved": approved,
                "estimate_cny": estimate.estimated_total_cny,
                "threshold_cny": self.threshold_cny,
                "tokens_in": estimate.estimated_tokens_in,
                "tokens_out": estimate.estimated_tokens_out,
                "llm_calls": estimate.estimated_llm_calls,
            },
        )

        return CostBudgetDecision(
            approved=approved,
            estimate=estimate,
            threshold_cny=self.threshold_cny,
            reason=reason,
        )


class CostBudgetExceeded(RuntimeError):
    """Raised when a build exceeds budget; pipeline catches and converts to user error."""

    def __init__(self, decision: CostBudgetDecision) -> None:
        super().__init__(decision.reason)
        self.decision = decision
