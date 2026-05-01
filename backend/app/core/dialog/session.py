from __future__ import annotations

import json
import uuid
from datetime import datetime

import aiosqlite
from pydantic import BaseModel, Field

from app.core.dialog.state_machine import DialogState
from app.core.llm.client import ChatMessage


class Session(BaseModel):
    """A persisted receptionist dialog session."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    state: DialogState = DialogState.COLLECTING
    messages: list[ChatMessage] = Field(default_factory=list)
    requirement_spec: dict[str, object] | None = None
    audit_log: list[dict[str, object]] = Field(
        default_factory=list,
        description=(
            "Per-turn audit entries (Batch K). Each item has shape "
            "{ts, kind, data}; kind ∈ {triage_decision, agent_invoked, workflow_planned}."
        ),
    )


class SessionStore:
    """SQLite-backed session persistence."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def create(self) -> Session:
        """Create and persist a new session."""
        session = Session()
        await self.save(session)
        return session

    async def get(self, session_id: str) -> Session | None:
        """Load a session by id."""
        await self._ensure_table()
        async with aiosqlite.connect(self._db_path) as connection:
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute(
                """
                SELECT id, created_at, state, messages_json, requirement_spec_json, audit_log_json
                FROM sessions
                WHERE id = ?
                """,
                (session_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        messages_json = row["messages_json"] or "[]"
        requirement_json = row["requirement_spec_json"]
        # audit_log_json column may be NULL for sessions created before Batch K
        audit_json = None
        try:
            audit_json = row["audit_log_json"]
        except (IndexError, KeyError):
            audit_json = None
        return Session(
            id=row["id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            state=DialogState(row["state"]),
            messages=[ChatMessage.model_validate(item) for item in json.loads(messages_json)],
            requirement_spec=json.loads(requirement_json) if requirement_json else None,
            audit_log=json.loads(audit_json) if audit_json else [],
        )

    async def save(self, session: Session) -> None:
        """Persist a session snapshot."""
        await self._ensure_table()
        async with aiosqlite.connect(self._db_path) as connection:
            await connection.execute(
                """
                INSERT OR REPLACE INTO sessions (
                    id, created_at, state, messages_json, requirement_spec_json, audit_log_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    session.id,
                    session.created_at.isoformat(),
                    session.state.value,
                    json.dumps([message.model_dump() for message in session.messages], ensure_ascii=False),
                    json.dumps(session.requirement_spec, ensure_ascii=False)
                    if session.requirement_spec is not None
                    else None,
                    json.dumps(session.audit_log, ensure_ascii=False, default=str)
                    if session.audit_log
                    else None,
                ),
            )
            await connection.commit()

    async def _ensure_table(self) -> None:
        async with aiosqlite.connect(self._db_path) as connection:
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    state TEXT NOT NULL,
                    messages_json TEXT NOT NULL,
                    requirement_spec_json TEXT,
                    audit_log_json TEXT
                )
                """
            )
            # Batch K — idempotent migration for older databases.
            try:
                await connection.execute(
                    "ALTER TABLE sessions ADD COLUMN audit_log_json TEXT"
                )
            except Exception:
                # Column already exists or other ALTER no-op; SQLite raises on
                # duplicate column adds — safe to swallow.
                pass
            await connection.commit()
