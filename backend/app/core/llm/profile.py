from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import aiosqlite
from pydantic import BaseModel, Field

from app.config import get_settings
from app.core.llm.client import ChatMessage, LLMClient


class ModelProfile(BaseModel):
    """Capability probe result for a given model."""

    model_id: str
    family: str = "unknown"
    context_window: int = 0
    json_reliability: float = 0.0
    function_calling_ok: bool = False
    long_context_ok: bool = False
    chinese_quality: float = 0.0
    needs_schema_reminder: bool = True
    recommended_temperature: float = 0.2
    max_retry: int = 3
    last_probed_at: datetime = Field(default_factory=datetime.utcnow)


class _JsonProbePayload(BaseModel):
    """Schema for the JSON compliance probe."""

    ok: bool
    items: list[str] = Field(..., min_length=2)


class _FunctionProbePayload(BaseModel):
    """Schema for the function calling probe."""

    tool_ready: bool


def _validate_json_schema_probe(content: str) -> float:
    payload = _JsonProbePayload.model_validate_json(_extract_json_candidate(content))
    return 1.0 if payload.ok else 0.0


def _validate_function_calling_probe(content: str) -> bool:
    payload = _FunctionProbePayload.model_validate_json(_extract_json_candidate(content))
    return payload.tool_ready


def _validate_long_context_probe(content: str) -> bool:
    return "LONG_CONTEXT_OK" in content


def _validate_chinese_generation_probe(content: str) -> float:
    has_chinese = any("\u4e00" <= char <= "\u9fff" for char in content)
    mentions_keywords = "首页" in content and "表单" in content
    return 1.0 if has_chinese and mentions_keywords else 0.0


PROBES: list[dict[str, Any]] = [
    {
        "name": "json_schema_compliance",
        "system_prompt": "Return strict JSON only.",
        "user_prompt": 'Reply with {"ok": true, "items": ["a", "b"]}.',
        "validator": _validate_json_schema_probe,
    },
    {
        "name": "function_calling",
        "system_prompt": "Acknowledge tool schema handling.",
        "user_prompt": 'Reply with {"tool_ready": true}.',
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "ack_probe",
                    "description": "A no-op tool for capability probing.",
                    "parameters": {"type": "object", "properties": {}, "required": []},
                },
            }
        ],
        "validator": _validate_function_calling_probe,
    },
    {
        "name": "long_context_8k",
        "system_prompt": "Read the entire prompt before answering.",
        "user_prompt": (
            "Read this filler and reply with LONG_CONTEXT_OK only.\n"
            f"{'0123456789abcdef' * 512}"
        ),
        "validator": _validate_long_context_probe,
    },
    {
        "name": "chinese_generation",
        "system_prompt": "Respond in concise Chinese.",
        "user_prompt": "用中文写一句包含“首页”和“表单”的自然短句。",
        "validator": _validate_chinese_generation_probe,
    },
]


class ProfileProber:
    """Runs and caches lightweight model capability probes."""

    def __init__(self, llm: LLMClient, db_path: str | None = None) -> None:
        self._llm = llm
        self._db_path = db_path or _resolve_db_path()

    async def probe(self, model_id: str, *, force: bool = False) -> ModelProfile:
        """Run all probes, cache the result, and return it."""
        if not force:
            cached = await self.get_cached(model_id)
            if cached is not None:
                return cached

        profile = ModelProfile(model_id=model_id, family=_infer_family(model_id))
        if not model_id.strip():
            await self._save(profile)
            return profile

        results: dict[str, Any] = {}
        for probe in PROBES:
            try:
                reply = await self._llm.chat(
                    model_id,
                    [
                        ChatMessage(role="system", content=probe["system_prompt"]),
                        ChatMessage(role="user", content=probe["user_prompt"]),
                    ],
                    tools=probe.get("tools"),
                    temperature=0.0,
                )
                validator: Callable[[str], Any] = probe["validator"]
                results[probe["name"]] = validator(reply.content)
            except Exception:
                continue

        profile.json_reliability = float(results.get("json_schema_compliance", 0.0))
        profile.function_calling_ok = bool(results.get("function_calling", False))
        profile.long_context_ok = bool(results.get("long_context_8k", False))
        profile.chinese_quality = float(results.get("chinese_generation", 0.0))
        profile.context_window = 8192 if profile.long_context_ok else 0
        profile.needs_schema_reminder = profile.json_reliability < 1.0
        profile.recommended_temperature = 0.1 if profile.needs_schema_reminder else 0.2
        profile.max_retry = 3 if profile.needs_schema_reminder else 2

        await self._save(profile)
        return profile

    async def get_cached(self, model_id: str) -> ModelProfile | None:
        """Return a cached profile when present."""
        await self._ensure_table()
        async with aiosqlite.connect(self._db_path) as connection:
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute(
                """
                SELECT model_id, family, context_window, json_reliability,
                       function_calling_ok, long_context_ok, chinese_quality,
                       needs_schema_reminder, recommended_temperature, max_retry,
                       last_probed_at
                FROM model_profiles
                WHERE model_id = ?
                """,
                (model_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return ModelProfile(
            model_id=row["model_id"],
            family=row["family"] or "unknown",
            context_window=int(row["context_window"] or 0),
            json_reliability=float(row["json_reliability"] or 0.0),
            function_calling_ok=bool(row["function_calling_ok"]),
            long_context_ok=bool(row["long_context_ok"]),
            chinese_quality=float(row["chinese_quality"] or 0.0),
            needs_schema_reminder=bool(row["needs_schema_reminder"]),
            recommended_temperature=float(row["recommended_temperature"] or 0.2),
            max_retry=int(row["max_retry"] or 3),
            last_probed_at=datetime.fromisoformat(row["last_probed_at"]),
        )

    async def _save(self, profile: ModelProfile) -> None:
        await self._ensure_table()
        async with aiosqlite.connect(self._db_path) as connection:
            await connection.execute(
                """
                INSERT OR REPLACE INTO model_profiles (
                    model_id, family, context_window, json_reliability,
                    function_calling_ok, long_context_ok, chinese_quality,
                    needs_schema_reminder, recommended_temperature, max_retry,
                    last_probed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    profile.model_id,
                    profile.family,
                    profile.context_window,
                    profile.json_reliability,
                    int(profile.function_calling_ok),
                    int(profile.long_context_ok),
                    profile.chinese_quality,
                    int(profile.needs_schema_reminder),
                    profile.recommended_temperature,
                    profile.max_retry,
                    profile.last_probed_at.isoformat(),
                ),
            )
            await connection.commit()

    async def _ensure_table(self) -> None:
        async with aiosqlite.connect(self._db_path) as connection:
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS model_profiles (
                    model_id TEXT PRIMARY KEY,
                    family TEXT,
                    context_window INTEGER,
                    json_reliability REAL,
                    function_calling_ok INTEGER,
                    long_context_ok INTEGER,
                    chinese_quality REAL,
                    needs_schema_reminder INTEGER,
                    recommended_temperature REAL,
                    max_retry INTEGER,
                    last_probed_at TEXT
                )
                """
            )
            await connection.commit()


def _extract_json_candidate(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1]).strip()
    starts = [index for index in (text.find("{"), text.find("[")) if index >= 0]
    if not starts:
        return text
    start = min(starts)
    end = max(text.rfind("}"), text.rfind("]"))
    if end >= start:
        return text[start : end + 1]
    return text


def _infer_family(model_id: str) -> str:
    normalized = model_id.strip().lower()
    if any(name in normalized for name in ("opus", "sonnet", "haiku")):
        return next(name for name in ("opus", "sonnet", "haiku") if name in normalized)
    return "unknown"


def _resolve_db_path() -> str:
    settings = get_settings()
    _ = settings.harness_env
    return str(Path(__file__).resolve().parents[4] / "harness.db")
