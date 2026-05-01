from __future__ import annotations

import asyncio
from pathlib import Path

import aiosqlite

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


async def init_db(db_path: str | Path = "harness.db") -> None:
    """Initialize the SQLite database from schema.sql."""
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    async with aiosqlite.connect(str(db_path)) as connection:
        await connection.executescript(schema_sql)
        await connection.commit()


def main() -> None:
    """CLI entrypoint for database initialization."""
    asyncio.run(init_db())
