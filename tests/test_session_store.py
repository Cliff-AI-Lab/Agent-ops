from __future__ import annotations

import pytest

from app.core.dialog.session import SessionStore
from app.core.dialog.state_machine import DialogState
from app.core.llm.client import ChatMessage


@pytest.mark.asyncio
async def test_session_store_create_get_save_round_trip(tmp_path) -> None:
    store = SessionStore(str(tmp_path / "sessions.db"))

    session = await store.create()
    session.state = DialogState.CONFIRMING
    session.messages.append(ChatMessage(role="user", content="hello"))
    session.requirement_spec = {"product_name": "Ops Board"}
    await store.save(session)

    loaded = await store.get(session.id)

    assert loaded is not None
    assert loaded.state == DialogState.CONFIRMING
    assert loaded.messages[0].content == "hello"
    assert loaded.requirement_spec == {"product_name": "Ops Board"}


@pytest.mark.asyncio
async def test_session_store_returns_none_for_missing_id(tmp_path) -> None:
    store = SessionStore(str(tmp_path / "sessions.db"))

    loaded = await store.get("missing")

    assert loaded is None


# ---------- Batch K — audit_log persistence ----------
@pytest.mark.asyncio
async def test_audit_log_round_trips(tmp_path) -> None:
    """audit_log entries survive a save → get cycle."""
    store = SessionStore(str(tmp_path / "audit.db"))
    session = await store.create()
    session.audit_log.append({
        "ts": "2026-04-26T17:00:00",
        "kind": "triage_decision",
        "data": {"target": "help", "target_id": None, "reason": "meta question"},
    })
    session.audit_log.append({
        "ts": "2026-04-26T17:00:05",
        "kind": "agent_invoked",
        "data": {"agent_id": "gen.x", "elapsed_ms": 12},
    })
    await store.save(session)
    loaded = await store.get(session.id)
    assert loaded is not None
    assert len(loaded.audit_log) == 2
    assert loaded.audit_log[0]["kind"] == "triage_decision"
    assert loaded.audit_log[0]["data"]["target"] == "help"
    assert loaded.audit_log[1]["data"]["agent_id"] == "gen.x"


@pytest.mark.asyncio
async def test_audit_log_default_empty(tmp_path) -> None:
    """A brand-new session has an empty audit_log (not None)."""
    store = SessionStore(str(tmp_path / "audit2.db"))
    session = await store.create()
    loaded = await store.get(session.id)
    assert loaded is not None
    assert loaded.audit_log == []


@pytest.mark.asyncio
async def test_audit_log_handles_legacy_db_without_column(tmp_path) -> None:
    """A pre-Batch-K database (no audit_log_json column) still loads — column added via ALTER TABLE."""
    import aiosqlite

    db_path = str(tmp_path / "legacy.db")
    # Create the legacy schema by hand (no audit_log_json column)
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("""
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                state TEXT NOT NULL,
                messages_json TEXT NOT NULL,
                requirement_spec_json TEXT
            )
        """)
        await conn.execute("""
            INSERT INTO sessions VALUES ('legacy-1', '2026-04-25T10:00:00', 'collecting', '[]', NULL)
        """)
        await conn.commit()

    store = SessionStore(db_path)
    loaded = await store.get("legacy-1")
    assert loaded is not None
    assert loaded.id == "legacy-1"
    assert loaded.audit_log == []  # Column added on first ensure_table, returns NULL → []
