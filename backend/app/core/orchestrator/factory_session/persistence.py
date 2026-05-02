"""SQLite persistence for FactorySession state machine.

Schema lives in backend/app/db/schema.sql (3 new tables: factory_sessions /
factory_artifacts / factory_gate_decisions).

Async via aiosqlite (matches existing v0.5.0 pattern).
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

import aiosqlite

from app.core.orchestrator.factory_session.state import FactorySessionState


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass
class SessionRecord:
    session_id: str
    nl: str
    state: FactorySessionState
    created_at: str
    updated_at: str
    final_run_id: str | None = None
    final_artifact_path: str | None = None
    industry_code: str | None = None
    scenario: str | None = None
    # Phase 5 multi-mode (V2.0.5+): 'design' (default) | 'variant' | 'production'
    mode: str = "design"


class SessionPersistence:
    """Async SQLite repository for FactorySession.

    Caller responsible for connection lifecycle (passes db_path).
    """

    def __init__(self, db_path: str = "harness.db") -> None:
        self._db_path = db_path

    async def create(
        self,
        nl: str,
        industry_code: str | None = None,
        scenario: str | None = None,
        mode: str = "design",
    ) -> SessionRecord:
        if mode not in ("design", "variant", "production"):
            raise ValueError(
                f"mode must be one of design/variant/production, got {mode!r}"
            )
        sid = uuid.uuid4().hex
        now = _now_iso()
        record = SessionRecord(
            session_id=sid,
            nl=nl,
            state=FactorySessionState.CREATED,
            created_at=now,
            updated_at=now,
            industry_code=industry_code,
            scenario=scenario,
            mode=mode,
        )
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO factory_sessions (
                    session_id, nl, state, created_at, updated_at,
                    industry_code, scenario, mode
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (sid, nl, record.state.value, now, now, industry_code, scenario, mode),
            )
            await db.commit()
        return record

    async def get(self, session_id: str) -> SessionRecord | None:
        async with aiosqlite.connect(self._db_path) as db:
            cur = await db.execute(
                "SELECT session_id, nl, state, created_at, updated_at, "
                "final_run_id, final_artifact_path, industry_code, scenario, mode "
                "FROM factory_sessions WHERE session_id = ?",
                (session_id,),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return SessionRecord(
            session_id=row[0],
            nl=row[1],
            state=FactorySessionState(row[2]),
            created_at=row[3],
            updated_at=row[4],
            final_run_id=row[5],
            final_artifact_path=row[6],
            industry_code=row[7],
            scenario=row[8],
            mode=row[9] if row[9] else "design",
        )

    async def update_state(
        self,
        session_id: str,
        state: FactorySessionState,
        final_run_id: str | None = None,
        final_artifact_path: str | None = None,
    ) -> None:
        now = _now_iso()
        async with aiosqlite.connect(self._db_path) as db:
            sets = ["state = ?", "updated_at = ?"]
            args: list[Any] = [state.value, now]
            if final_run_id is not None:
                sets.append("final_run_id = ?")
                args.append(final_run_id)
            if final_artifact_path is not None:
                sets.append("final_artifact_path = ?")
                args.append(final_artifact_path)
            args.append(session_id)
            await db.execute(
                f"UPDATE factory_sessions SET {', '.join(sets)} WHERE session_id = ?",
                tuple(args),
            )
            await db.commit()

    async def save_artifact(
        self,
        session_id: str,
        stage: str,
        run_id: str,
        payload: dict,
    ) -> str:
        artifact_id = uuid.uuid4().hex
        now = _now_iso()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO factory_artifacts (
                    artifact_id, session_id, stage, run_id, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (artifact_id, session_id, stage, run_id, json.dumps(payload, ensure_ascii=False), now),
            )
            await db.commit()
        return artifact_id

    async def latest_artifact(
        self, session_id: str, stage: str
    ) -> tuple[str, str, dict] | None:
        async with aiosqlite.connect(self._db_path) as db:
            cur = await db.execute(
                """
                SELECT artifact_id, run_id, payload_json
                FROM factory_artifacts
                WHERE session_id = ? AND stage = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (session_id, stage),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return (row[0], row[1], json.loads(row[2]))

    async def record_gate_decision(
        self,
        session_id: str,
        gate_id: str,
        decision: str,
        payload: dict | None = None,
        decided_by: str | None = None,
    ) -> int:
        now = _now_iso()
        async with aiosqlite.connect(self._db_path) as db:
            cur = await db.execute(
                """
                INSERT INTO factory_gate_decisions (
                    session_id, gate_id, decision, payload_json, decided_by, decided_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    gate_id,
                    decision,
                    json.dumps(payload, ensure_ascii=False) if payload else None,
                    decided_by,
                    now,
                ),
            )
            await db.commit()
            return int(cur.lastrowid or 0)

    async def list_gate_decisions(
        self, session_id: str
    ) -> list[dict]:
        async with aiosqlite.connect(self._db_path) as db:
            cur = await db.execute(
                """
                SELECT id, gate_id, decision, payload_json, decided_by, decided_at
                FROM factory_gate_decisions WHERE session_id = ?
                ORDER BY decided_at ASC
                """,
                (session_id,),
            )
            rows = await cur.fetchall()
        return [
            {
                "id": r[0],
                "gate_id": r[1],
                "decision": r[2],
                "payload": json.loads(r[3]) if r[3] else None,
                "decided_by": r[4],
                "decided_at": r[5],
            }
            for r in rows
        ]
