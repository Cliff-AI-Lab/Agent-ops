"""CostLedger - V2.2 W2 persistence for rolling-budget tracking.

Records every cost-gated build (approved + refused) for daily/monthly
aggregation. RollingBudget queries it to enforce per-day / per-month caps.

Storage: SQLite via aiosqlite (same DB as factory_sessions).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import aiosqlite

from app.core.governance.interface import CostEstimate


def _now() -> tuple[str, str, str]:
    """(iso, YYYY-MM-DD, YYYY-MM) in UTC."""
    n = datetime.now(timezone.utc)
    return (
        n.isoformat(),
        n.strftime("%Y-%m-%d"),
        n.strftime("%Y-%m"),
    )


class CostLedger:
    """Async SQLite repo for cost_ledger table."""

    def __init__(self, db_path: str = "harness.db") -> None:
        self._db_path = db_path

    async def record(
        self,
        estimate: CostEstimate,
        approved: bool,
        path: str,
        tenant_id: str = "default",
        session_id: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> None:
        """Record one build attempt (approved or refused)."""
        if path not in ("single", "multi"):
            raise ValueError(f"path must be 'single' or 'multi', got {path!r}")
        ts, date_utc, month_utc = _now()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO cost_ledger (
                  ts, date_utc, month_utc, tenant_id, path,
                  estimated_cny, tokens_in, tokens_out, llm_calls,
                  approved, session_id, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts,
                    date_utc,
                    month_utc,
                    tenant_id,
                    path,
                    estimate.estimated_total_cny,
                    estimate.estimated_tokens_in,
                    estimate.estimated_tokens_out,
                    estimate.estimated_llm_calls,
                    1 if approved else 0,
                    session_id,
                    notes,
                ),
            )
            await db.commit()

    async def daily_spent(
        self, tenant_id: str = "default", date_utc: Optional[str] = None
    ) -> float:
        """Sum estimated_cny for a tenant on a given UTC day (defaults: today).

        Only counts approved builds (refused builds didn't actually spend).
        """
        if date_utc is None:
            date_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        async with aiosqlite.connect(self._db_path) as db:
            cur = await db.execute(
                "SELECT COALESCE(SUM(estimated_cny), 0) "
                "FROM cost_ledger "
                "WHERE tenant_id = ? AND date_utc = ? AND approved = 1",
                (tenant_id, date_utc),
            )
            row = await cur.fetchone()
        return float(row[0]) if row else 0.0

    async def monthly_spent(
        self, tenant_id: str = "default", month_utc: Optional[str] = None
    ) -> float:
        """Sum estimated_cny for a tenant in a given UTC month (defaults: now)."""
        if month_utc is None:
            month_utc = datetime.now(timezone.utc).strftime("%Y-%m")
        async with aiosqlite.connect(self._db_path) as db:
            cur = await db.execute(
                "SELECT COALESCE(SUM(estimated_cny), 0) "
                "FROM cost_ledger "
                "WHERE tenant_id = ? AND month_utc = ? AND approved = 1",
                (tenant_id, month_utc),
            )
            row = await cur.fetchone()
        return float(row[0]) if row else 0.0

    async def count(self, tenant_id: str = "default") -> int:
        async with aiosqlite.connect(self._db_path) as db:
            cur = await db.execute(
                "SELECT COUNT(*) FROM cost_ledger WHERE tenant_id = ?",
                (tenant_id,),
            )
            row = await cur.fetchone()
        return int(row[0]) if row else 0
