"""Tests for V2.3 governance extensions: TiktokenCostEstimator + SoftWarnBudget."""
from __future__ import annotations

import pytest

from app.core.governance import (
    CostEstimate,
    HeuristicCostEstimator,
    SoftWarnBudget,
    ThresholdCostBudget,
    TiktokenCostEstimator,
)


# ---- TiktokenCostEstimator -----------------------------------------------


def test_tiktoken_estimator_constructs():
    est = TiktokenCostEstimator()
    # Either successful tiktoken init OR fallback — both valid
    assert est.encoding_name in ("cl100k_base", "char-proxy-fallback")


def test_tiktoken_estimate_single_returns_cost():
    est = TiktokenCostEstimator()
    e = est.estimate_single("做一份销售周报")
    assert e.estimated_total_cny > 0
    assert e.estimated_llm_calls == 3
    # Confidence higher than V2.2 W1 heuristic when tiktoken available
    if est.encoding_name == "cl100k_base":
        assert e.confidence == 0.85


def test_tiktoken_estimate_multi_scales_with_specialists():
    est = TiktokenCostEstimator()
    e3 = est.estimate_multi("test", anticipated_specialists=3)
    e10 = est.estimate_multi("test", anticipated_specialists=10)
    assert e10.estimated_total_cny > e3.estimated_total_cny


def test_tiktoken_real_token_count_differs_from_char_proxy():
    """For mixed Chinese/English, tiktoken should give a different (usually lower)
    token count than naive char count for the same input."""
    tk = TiktokenCostEstimator()
    if tk.encoding_name != "cl100k_base":
        pytest.skip("tiktoken not available; nothing to compare")
    heur = HeuristicCostEstimator()
    nl = "do an airline customer service multi-agent system"  # english heavy
    tk_est = tk.estimate_single(nl)
    heur_est = heur.estimate_single(nl)
    # English text has fewer tokens than chars; tiktoken should have fewer in-tokens.
    assert tk_est.estimated_tokens_in < heur_est.estimated_tokens_in


def test_tiktoken_notes_mention_encoding():
    est = TiktokenCostEstimator()
    e = est.estimate_single("test")
    assert any("tiktoken" in n or "encoding" in n for n in e.notes)


def test_tiktoken_inherits_pricing_overrides():
    """Custom pricing flows through as in HeuristicCostEstimator."""
    cheap = TiktokenCostEstimator(price_input_per_1k_cny=0.001, price_output_per_1k_cny=0.001)
    expensive = TiktokenCostEstimator(price_input_per_1k_cny=1.0, price_output_per_1k_cny=1.0)
    a = cheap.estimate_single("test").estimated_total_cny
    b = expensive.estimate_single("test").estimated_total_cny
    assert a < b


# ---- SoftWarnBudget ------------------------------------------------------


def _est(total: float) -> CostEstimate:
    return CostEstimate(
        estimated_total_cny=total,
        breakdown={"intent_parser": total},
        estimated_llm_calls=1,
        estimated_tokens_in=100,
        estimated_tokens_out=200,
        notes=[],
        confidence=0.5,
    )


def test_soft_warn_constructor_rejects_invalid_ratio():
    base = ThresholdCostBudget(threshold_cny=1.0)
    with pytest.raises(ValueError, match="warn_ratio"):
        SoftWarnBudget(underlying=base, warn_ratio=0.0)
    with pytest.raises(ValueError, match="warn_ratio"):
        SoftWarnBudget(underlying=base, warn_ratio=1.0)
    with pytest.raises(ValueError, match="warn_ratio"):
        SoftWarnBudget(underlying=base, warn_ratio=1.5)


def test_soft_warn_threshold_proxies_underlying():
    base = ThresholdCostBudget(threshold_cny=2.50)
    warn = SoftWarnBudget(underlying=base, warn_ratio=0.80)
    assert warn.threshold_cny == 2.50
    assert warn.warn_threshold_cny == 2.0  # 80% of 2.50


def test_soft_warn_approves_below_warn_threshold():
    base = ThresholdCostBudget(threshold_cny=1.00)
    warn = SoftWarnBudget(underlying=base, warn_ratio=0.80)
    decision = warn.check(_est(0.30))  # 30% — below 80% warn
    assert decision.approved is True


def test_soft_warn_approves_at_warn_threshold():
    """At/above warn threshold but below hard cap: approve + warn (no exception)."""
    base = ThresholdCostBudget(threshold_cny=1.00)
    warn = SoftWarnBudget(underlying=base, warn_ratio=0.80)
    decision = warn.check(_est(0.85))  # 85% — above warn (80%) below cap (100%)
    assert decision.approved is True


def test_soft_warn_blocks_over_hard_cap():
    """Above hard cap: refuse (delegates to underlying)."""
    base = ThresholdCostBudget(threshold_cny=1.00)
    warn = SoftWarnBudget(underlying=base, warn_ratio=0.80)
    decision = warn.check(_est(1.50))
    assert decision.approved is False


@pytest.mark.asyncio
async def test_soft_warn_async_path_works_with_rolling_budget(tmp_path):
    """SoftWarnBudget.check_async dispatches to RollingBudget.check_async correctly."""
    import aiosqlite
    from app.core.governance import CostLedger, RollingBudget

    SCHEMA = """
    CREATE TABLE cost_ledger (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ts TEXT NOT NULL, date_utc TEXT NOT NULL, month_utc TEXT NOT NULL,
      tenant_id TEXT NOT NULL DEFAULT 'default',
      path TEXT NOT NULL, estimated_cny REAL NOT NULL,
      tokens_in INTEGER NOT NULL, tokens_out INTEGER NOT NULL,
      llm_calls INTEGER NOT NULL, approved INTEGER NOT NULL,
      session_id TEXT, notes TEXT
    );
    """
    db = str(tmp_path / "softwarn.db")
    async with aiosqlite.connect(db) as conn:
        await conn.executescript(SCHEMA)
        await conn.commit()

    ledger = CostLedger(db_path=db)
    rolling = RollingBudget(ledger=ledger, daily_cap_cny=10.0)
    warn = SoftWarnBudget(underlying=rolling, warn_ratio=0.8)
    decision = await warn.check_async(_est(0.5))
    assert decision.approved is True
    assert warn.threshold_cny == 10.0


def test_soft_warn_default_ratio_is_80_percent():
    base = ThresholdCostBudget(threshold_cny=10.0)
    warn = SoftWarnBudget(underlying=base)
    assert warn.warn_threshold_cny == 8.0
