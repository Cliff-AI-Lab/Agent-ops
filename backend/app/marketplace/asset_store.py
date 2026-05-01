"""AssetStore — SQLite-backed persistence for GeneratedAsset (Batch X)."""
from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from app.ontology import GeneratedAsset

_LOG = logging.getLogger("agent_ops.marketplace.asset_store")


def _project_root() -> Path:
    """Repo root (sibling of ``backend/``)."""
    return Path(__file__).resolve().parents[3]


def default_assets_root() -> Path:
    """``assets/`` directory at the project root (where zips live)."""
    return _project_root() / "assets"


def default_generated_agents_dir() -> Path:
    """``agents/__generated__/`` where auto-registered agent.yamls live."""
    return _project_root() / "agents" / "__generated__"


def default_db_path() -> Path:
    return _project_root() / "harness.db"


def _row_to_asset(row: sqlite3.Row | tuple) -> GeneratedAsset:
    columns = [
        "asset_id", "name", "description", "product_type",
        "source_session_id", "source_sop", "generation_id", "generated_at",
        "artifact_path", "agent_yaml_path", "file_count", "total_bytes",
        "intent_keywords_json", "tags_json", "capabilities_used_json", "tools_used_json",
        "quality_score", "usage_count", "promoted_by", "promoted_at", "status",
    ]
    if isinstance(row, sqlite3.Row):
        data = dict(row)
    else:
        data = dict(zip(columns, row))
    return GeneratedAsset(
        asset_id=data["asset_id"],
        name=data["name"],
        description=data.get("description") or "",
        product_type=data.get("product_type") or "app",
        source_session_id=data.get("source_session_id"),
        source_sop=data.get("source_sop"),
        generation_id=data.get("generation_id"),
        generated_at=datetime.fromisoformat(data["generated_at"]),
        artifact_path=data["artifact_path"],
        agent_yaml_path=data.get("agent_yaml_path"),
        file_count=data.get("file_count") or 0,
        total_bytes=data.get("total_bytes") or 0,
        intent_keywords=json.loads(data.get("intent_keywords_json") or "[]"),
        tags=json.loads(data.get("tags_json") or "[]"),
        capabilities_used=json.loads(data.get("capabilities_used_json") or "[]"),
        tools_used=json.loads(data.get("tools_used_json") or "[]"),
        quality_score=data.get("quality_score"),
        usage_count=data.get("usage_count") or 0,
        promoted_by=data.get("promoted_by"),
        promoted_at=datetime.fromisoformat(data["promoted_at"]) if data.get("promoted_at") else None,
        status=data.get("status") or "draft",
    )


class AssetStore:
    """Thin SQLite wrapper. Ensures the ``generated_assets`` table exists."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self._db_path = Path(db_path) if db_path else default_db_path()
        self._ensure_table()

    # ---- Schema bootstrap (idempotent — only creates if missing) ----
    def _ensure_table(self) -> None:
        with sqlite3.connect(self._db_path) as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS generated_assets (
                    asset_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    product_type TEXT NOT NULL DEFAULT 'app',
                    source_session_id TEXT,
                    source_sop TEXT,
                    generation_id TEXT,
                    generated_at TEXT NOT NULL,
                    artifact_path TEXT NOT NULL,
                    agent_yaml_path TEXT,
                    file_count INT DEFAULT 0,
                    total_bytes INT DEFAULT 0,
                    intent_keywords_json TEXT DEFAULT '[]',
                    tags_json TEXT DEFAULT '[]',
                    capabilities_used_json TEXT DEFAULT '[]',
                    tools_used_json TEXT DEFAULT '[]',
                    quality_score REAL,
                    usage_count INT DEFAULT 0,
                    promoted_by TEXT,
                    promoted_at TEXT,
                    status TEXT NOT NULL DEFAULT 'draft'
                )
                """
            )
            con.execute("CREATE INDEX IF NOT EXISTS idx_assets_status ON generated_assets(status)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_assets_session ON generated_assets(source_session_id)")
            con.commit()

    # ---- CRUD ----
    def register(self, asset: GeneratedAsset) -> None:
        with sqlite3.connect(self._db_path) as con:
            con.execute(
                """
                INSERT OR REPLACE INTO generated_assets
                (asset_id, name, description, product_type,
                 source_session_id, source_sop, generation_id, generated_at,
                 artifact_path, agent_yaml_path, file_count, total_bytes,
                 intent_keywords_json, tags_json, capabilities_used_json, tools_used_json,
                 quality_score, usage_count, promoted_by, promoted_at, status)
                VALUES (?,?,?,?, ?,?,?,?, ?,?,?,?, ?,?,?,?, ?,?,?,?,?)
                """,
                (
                    asset.asset_id, asset.name, asset.description, asset.product_type,
                    asset.source_session_id, asset.source_sop, asset.generation_id,
                    asset.generated_at.isoformat(),
                    asset.artifact_path, asset.agent_yaml_path,
                    asset.file_count, asset.total_bytes,
                    json.dumps(asset.intent_keywords, ensure_ascii=False),
                    json.dumps(asset.tags, ensure_ascii=False),
                    json.dumps(asset.capabilities_used, ensure_ascii=False),
                    json.dumps(asset.tools_used, ensure_ascii=False),
                    asset.quality_score, asset.usage_count,
                    asset.promoted_by,
                    asset.promoted_at.isoformat() if asset.promoted_at else None,
                    asset.status,
                ),
            )
            con.commit()

    def get(self, asset_id: str) -> GeneratedAsset | None:
        with sqlite3.connect(self._db_path) as con:
            con.row_factory = sqlite3.Row
            cur = con.execute(
                "SELECT * FROM generated_assets WHERE asset_id = ?", (asset_id,)
            )
            row = cur.fetchone()
            return _row_to_asset(row) if row else None

    def list(
        self,
        *,
        status: str | None = None,
        product_type: str | None = None,
        session_id: str | None = None,
        limit: int = 200,
    ) -> list[GeneratedAsset]:
        sql = "SELECT * FROM generated_assets WHERE 1=1"
        params: list[object] = []
        if status:
            sql += " AND status = ?"
            params.append(status)
        if product_type:
            sql += " AND product_type = ?"
            params.append(product_type)
        if session_id:
            sql += " AND source_session_id = ?"
            params.append(session_id)
        sql += " ORDER BY generated_at DESC LIMIT ?"
        params.append(limit)

        with sqlite3.connect(self._db_path) as con:
            con.row_factory = sqlite3.Row
            cur = con.execute(sql, params)
            return [_row_to_asset(r) for r in cur.fetchall()]

    def promote(self, asset_id: str, promoted_by: str = "system") -> GeneratedAsset | None:
        asset = self.get(asset_id)
        if asset is None:
            return None
        asset.status = "active"
        asset.promoted_by = promoted_by
        asset.promoted_at = datetime.utcnow()
        self.register(asset)
        return asset

    def archive(self, asset_id: str) -> GeneratedAsset | None:
        asset = self.get(asset_id)
        if asset is None:
            return None
        asset.status = "archived"
        self.register(asset)
        return asset

    def increment_usage(self, asset_id: str) -> None:
        with sqlite3.connect(self._db_path) as con:
            con.execute(
                "UPDATE generated_assets SET usage_count = usage_count + 1 WHERE asset_id = ?",
                (asset_id,),
            )
            con.commit()

    def stats(self) -> dict[str, int]:
        with sqlite3.connect(self._db_path) as con:
            cur = con.execute(
                "SELECT status, COUNT(*) FROM generated_assets GROUP BY status"
            )
            counts: dict[str, int] = {}
            for status, n in cur.fetchall():
                counts[status] = n
            counts["total"] = sum(counts.values())
            return counts


# ---- Singleton accessors ----
_asset_store: AssetStore | None = None


def get_asset_store() -> AssetStore:
    global _asset_store
    if _asset_store is None:
        _asset_store = AssetStore()
    return _asset_store


def reset_asset_store(db_path: Path | str | None = None) -> AssetStore:
    global _asset_store
    _asset_store = AssetStore(db_path)
    return _asset_store


def bootstrap_asset_store() -> AssetStore:
    """Ensure the table exists, return the singleton."""
    return get_asset_store()
