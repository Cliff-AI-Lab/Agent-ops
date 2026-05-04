"""Tests for ThresholdCostBudget."""
from __future__ import annotations

import pytest

from app.core.governance import (
    CostBudgetExceeded,
    CostEstimate,
    HeuristicCostEstimator,
    ThresholdCostBudget,
)


def _est(total: float) -> CostEstimate:
    return CostEstimate(
        estimated_total_cny=total,
        breakdown={"intent_parser": total},
        estimated_llm_calls=1,
        estimated_tokens_in=100,
        estimated_tokens_out=200,
        notes=["test"],
        confidence=0.5,
    )


def test_constructor_rejects_zero_threshold():
    with pytest.raises(ValueError, match="positive"):
        ThresholdCostBudget(threshold_cny=0)


def test_constructor_rejects_negative_threshold():
    with pytest.raises(ValueError, match="positive"):
        ThresholdCostBudget(threshold_cny=-0.5)


def test_check_approves_under_threshold():
    budget = ThresholdCostBudget(threshold_cny=1.00)
    decision = budget.check(_est(0.50))
    assert decision.approved is True
    assert "0.5" in decision.reason or "0.50" in decision.reason


def test_check_approves_at_threshold():
    """Boundary: estimate == threshold is approved."""
    budget = ThresholdCostBudget(threshold_cny=1.00)
    decision = budget.check(_est(1.00))
    assert decision.approved is True


def test_check_rejects_over_threshold():
    budget = ThresholdCostBudget(threshold_cny=1.00)
    decision = budget.check(_est(1.50))
    assert decision.approved is False
    assert "exceeds" in decision.reason


def test_decision_carries_estimate():
    budget = ThresholdCostBudget(threshold_cny=2.00)
    e = _est(0.75)
    decision = budget.check(e)
    assert decision.estimate is e
    assert decision.threshold_cny == 2.00


def test_cost_budget_exceeded_exception_carries_decision():
    budget = ThresholdCostBudget(threshold_cny=0.10)
    decision = budget.check(_est(1.00))
    assert decision.approved is False
    exc = CostBudgetExceeded(decision)
    assert exc.decision is decision
    assert "exceeds" in str(exc)


def test_integration_estimator_and_budget_default_safe():
    """A default heuristic single-agent build should fit a 1.00 CNY budget."""
    estimator = HeuristicCostEstimator()
    budget = ThresholdCostBudget(threshold_cny=1.00)
    e = estimator.estimate_single("生成销售周报")
    decision = budget.check(e)
    # Default heuristic for short NL should fit 1 CNY easily
    assert decision.approved is True


def test_integration_low_budget_blocks_multi_agent():
    """A 0.01 CNY budget should fail any realistic multi-agent build."""
    estimator = HeuristicCostEstimator()
    budget = ThresholdCostBudget(threshold_cny=0.01)
    e = estimator.estimate_multi("做一个客服系统", anticipated_specialists=5)
    decision = budget.check(e)
    assert decision.approved is False
