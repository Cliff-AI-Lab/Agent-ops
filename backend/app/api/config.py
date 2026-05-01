from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.config import get_settings
from app.core.llm.client import LLMClient
from app.core.llm.errors import ConfigError

router = APIRouter(prefix="/api/config", tags=["config"])


def _mask_key(key: str | None) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:5]}...{key[-3:]}"


@router.get("")
async def get_config() -> dict[str, object]:
    """Return sanitized runtime configuration + available chat models."""
    settings = get_settings()
    payload: dict[str, object] = {
        "env": settings.harness_env,
        "sandbox_api_base": settings.sandbox_api_base,
        "api_key_masked": _mask_key(settings.ruidong_api_key),
        "api_key_configured": bool(settings.ruidong_api_key),
        "default_model": settings.harness_default_model,
        "chat_models": [],
    }
    if not settings.ruidong_api_key:
        return payload
    try:
        client = LLMClient()
    except ConfigError:
        return payload
    try:
        models = await client.list_models()
        payload["chat_models"] = [m.id for m in models]
    except Exception as exc:
        payload["models_error"] = str(exc)
    finally:
        await client.aclose()
    return payload
