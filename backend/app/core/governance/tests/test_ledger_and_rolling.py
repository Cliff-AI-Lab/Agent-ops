"""Tests for CostLedger + RollingBudget (V2.2 W2)."""
from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

from app.core.governance import (
    CostEstimate,
    CostLedger,
    RollingBudget,
)


SCHEMA = """
CREATE TABLE cost_ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  date_utc TEXT NOT NULL,
  month_utc TEXT NOT NULL,
  tenant_id TEXT NOT NULL DEFAULT 'default',
  path TEXT NOT NULL,
  estimated_cny REAL NOT NULL,
  tokens_in INTEGER NOT NULL,
  tokens_out INTEGER NOT NULL,
  llm_calls INTEGER NOT NULL,
  approved INTEGER NOT NULL,
  session_id TEXT,
  notes TEXT
);
CREATE INDEX idx_cost_ledger_date ON cost_ledger(date_utc, tenant_id);
CREATE INDEX idx_cost_ledger_month ON cost_ledger(month_utc, tenant_id);
"""


def _est(total: float) -> CostEstimate:
    return CostEstimate(
        estimated_total_cny=total,
        breakdown={"intent_parser": total},
        estimated_llm_calls=2,
        estimated_tokens_in=300,
        estimated_tokens_out=400,
        notes=[],
        confidence=0.7,
    )


@pytest.fixture
async def temp_db(tmp_path: Path) -> str:
    db_path = str(tmp_path / "test_governance.db")
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(SCHEMA)
        await db.commit()
    return db_path


# ---- CostLedger ---------------------------------------------------------


@pytest.mark.asyncio
async def test_ledger_records_and_counts(temp_db):
    ledger = CostLedger(db_path=temp_db)
    await ledger.record(_est(0.10), approved=True, path="single")
    await ledger.record(_est(0.20), approved=False, path="multi")
    assert await ledger.count() == 2


@pytest.mark.asyncio
async def test_ledger_daily_spent_only_counts_approved(temp_db):
    ledger = CostLedger(db_path=temp_db)
    await ledger.record(_est(0.30), approved=True, path="single")
    await ledger.record(_est(0.50), approved=True, path="multi")
    await ledger.record(_est(99.0), approved=False, path="multi")  # refused doesn't count
    spent = await ledger.daily_spent()
    assert abs(spent - 0.80) < 1e-6


@pytest.mark.asyncio
async def test_ledger_isolates_by_tenant(temp_db):
    ledger = CostLedger(db_path=temp_db)
    await ledger.record(_est(0.10), approved=True, path="single", tenant_id="alice")
    await ledger.record(_est(0.20), approved=True, path="single", tenant_id="bob")
    assert abs(await ledger.daily_spent(tenant_id="alice") - 0.10) < 1e-6
    assert abs(await ledger.daily_spent(tenant_id="bob") - 0.20) < 1e-6


@pytest.mark.asyncio
async def test_ledger_rejects_unknown_path(temp_db):
    ledger = CostLedger(db_path=temp_db)
    with pytest.raises(ValueError, match="path must"):
        await ledger.record(_est(0.10), approved=True, path="bogus")


# ---- RollingBudget ------------------------------------------------------


def test_rolling_budget_requires_at_least_one_cap(temp_db):
    ledger = CostLedger(db_path=temp_db)
    with pytest.raises(ValueError, match="at least one"):
        RollingBudget(ledger=ledger)


def test_rolling_budget_rejects_zero_cap(temp_db):
    ledger = CostLedger(db_path=temp_db)
    with pytest.raises(ValueError, match="positive"):
        RollingBudget(ledger=ledger, daily_cap_cny=0)


def test_rolling_budget_threshold_uses_min_cap(temp_db):
    ledger = CostLedger(db_path=temp_db)
    b = RollingBudget(ledger=ledger, daily_cap_cny=2.0, monthly_cap_cny=10.0)
    assert b.threshold_cny == 2.0  # min of 2 and 10
    b2 = RollingBudget(ledger=ledger, daily_cap_cny=20.0, monthly_cap_cny=10.0)
    assert b2.threshold_cny == 10.0  # min flips


@pytest.mark.asyncio
async def test_rolling_approves_first_build(temp_db):
    ledger = CostLedger(db_path=temp_db)
    budget = RollingBudget(ledger=ledger, daily_cap_cny=1.00)
    decision = await budget.check_async(_est(0.50))
    assert decision.approved is True


@pytest.mark.asyncio
async def test_rolling_refuses_when_today_exceeds_daily_cap(temp_db):
    ledger = CostLedger(db_path=temp_db)
    # Pre-fill ledger with 0.80 spent today
    await ledger.record(_est(0.80), approved=True, path="single")
    budget = RollingBudget(ledger=ledger, daily_cap_cny=1.00)
    # This 0.30 build would push today total to 1.10 > 1.00
    decision = await budget.check_async(_est(0.30))
    assert decision.approved is False
    assert "daily" in decision.reason


@pytest.mark.asyncio
async def test_rolling_refuses_when_month_exceeds_monthly_cap(temp_db):
    ledger = CostLedger(db_path=temp_db)
    # Pre-fill 9.50 over the month
    await ledger.record(_est(9.50), approved=True, path="single")
    budget = RollingBudget(ledger=ledger, daily_cap_cny=100.0, monthly_cap_cny=10.0)
    decision = await budget.check_async(_est(0.60))
    assert decision.approved is False
    assert "monthly" in decision.reason


@pytest.mark.asyncio
async def test_rolling_isolates_tenants(temp_db):
    ledger = CostLedger(db_path=temp_db)
    # alice spent 0.90 today
    await ledger.record(_est(0.90), approved=True, path="single", tenant_id="alice")
    # alice's budget can't take 0.30 more (cap 1.00)
    alice_b = RollingBudget(
        ledger=ledger, daily_cap_cny=1.00, tenant_id="alice"
    )
    decision = await alice_b.check_async(_est(0.30))
    assert decision.approved is False

    # bob is fresh — can take it
    bob_b = RollingBudget(
        ledger=ledger, daily_cap_cny=1.00, tenant_id="bob"
    )
    decision = await bob_b.check_async(_est(0.30))
    assert decision.approved is True


@pytest.mark.asyncio
async def test_rolling_reason_contains_numbers(temp_db):
    ledger = CostLedger(db_path=temp_db)
    await ledger.record(_est(0.40), approved=True, path="single")
    budget = RollingBudget(ledger=ledger, daily_cap_cny=1.0, monthly_cap_cny=10.0)
    decision = await budget.check_async(_est(0.20))
    assert decision.approved is True
    assert "0.4" in decision.reason  # spent today
    # Don't be too fragile about exact format
