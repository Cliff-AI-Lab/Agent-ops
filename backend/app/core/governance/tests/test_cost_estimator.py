"""Tests for HeuristicCostEstimator (V2.2 W1)."""
from __future__ import annotations

import pytest

from app.core.governance import (
    DEFAULT_PRICE_INPUT_PER_1K_CNY,
    DEFAULT_PRICE_OUTPUT_PER_1K_CNY,
    HeuristicCostEstimator,
)


def test_constructor_defaults():
    est = HeuristicCostEstimator()
    assert est.in_price == DEFAULT_PRICE_INPUT_PER_1K_CNY
    assert est.out_price == DEFAULT_PRICE_OUTPUT_PER_1K_CNY


def test_estimate_single_returns_positive_cost():
    est = HeuristicCostEstimator()
    e = est.estimate_single("做一份销售周报")
    assert e.estimated_total_cny > 0
    assert e.estimated_llm_calls == 3
    assert e.estimated_tokens_in > 0
    assert e.estimated_tokens_out > 0


def test_estimate_single_breakdown_sums_to_total():
    est = HeuristicCostEstimator()
    e = est.estimate_single("test build")
    total = sum(e.breakdown.values())
    assert abs(total - e.estimated_total_cny) < 1e-6


def test_estimate_single_includes_known_components():
    est = HeuristicCostEstimator()
    e = est.estimate_single("test")
    assert "intent_parser" in e.breakdown
    assert "resolver" in e.breakdown
    assert "compiler" in e.breakdown
    assert e.breakdown["compiler"] == 0.0  # template, no LLM


def test_estimate_multi_returns_positive_cost():
    est = HeuristicCostEstimator()
    e = est.estimate_multi("做一个客服系统")
    assert e.estimated_total_cny > 0
    assert e.estimated_llm_calls == 2


def test_estimate_multi_scales_with_specialists():
    est = HeuristicCostEstimator()
    e3 = est.estimate_multi("test", anticipated_specialists=3)
    e10 = est.estimate_multi("test", anticipated_specialists=10)
    assert e10.estimated_total_cny > e3.estimated_total_cny
    assert e10.estimated_tokens_out > e3.estimated_tokens_out


def test_estimate_multi_clamps_specialists_to_at_least_one():
    """Negative or zero specialist count should be coerced to 1."""
    est = HeuristicCostEstimator()
    e0 = est.estimate_multi("test", anticipated_specialists=0)
    e1 = est.estimate_multi("test", anticipated_specialists=1)
    assert e0.estimated_total_cny == e1.estimated_total_cny


def test_custom_pricing_lowers_total():
    cheap = HeuristicCostEstimator(
        price_input_per_1k_cny=0.001, price_output_per_1k_cny=0.001
    )
    expensive = HeuristicCostEstimator(
        price_input_per_1k_cny=1.0, price_output_per_1k_cny=1.0
    )
    nl = "test build something"
    assert cheap.estimate_single(nl).estimated_total_cny < expensive.estimate_single(nl).estimated_total_cny


def test_estimate_includes_descriptive_notes():
    est = HeuristicCostEstimator()
    e = est.estimate_single("test")
    assert any("heuristic" in n for n in e.notes)
    assert any("prices:" in n for n in e.notes)


def test_estimate_confidence_in_range():
    est = HeuristicCostEstimator()
    assert 0.0 <= est.estimate_single("x").confidence <= 1.0
    assert 0.0 <= est.estimate_multi("x").confidence <= 1.0
    # Multi has lower confidence than single (anticipated specialists is unknown)
    assert est.estimate_multi("x").confidence < est.estimate_single("x").confidence
