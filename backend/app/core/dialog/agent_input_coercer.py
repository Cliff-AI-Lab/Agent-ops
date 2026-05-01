"""Coerce a free-text user message into a generated agent's declared input_schema (Batch I-2).

When :class:`TurnOrchestrator` routes a turn to a generated agent, the agent's
:class:`AgentContract.input_schema` may declare structured fields. The user
typed prose, not JSON — so we need to bridge:

    SCHEMA_EMPTY    →  {"message": user_msg}                 (default)
    SCHEMA_NAMED    →  heuristic key-match  {"query": ...}    (free)
    SCHEMA_COMPLEX  →  one light LLM call to coerce          (cheap)
    LLM_FAILS       →  fall back to {"message": user_msg}    (best effort)

The coercer never blocks the turn — it always returns *some* payload + a
``method`` tag so the audit log records what happened.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.core.llm.client import ChatMessage, LLMClient
from app.core.trace.bus import emit


_HEURISTIC_KEYS = ("message", "query", "input", "text", "prompt", "user_input", "request")


@dataclass
class CoercionResult:
    inputs: dict[str, Any]
    method: str   # "empty_schema" | "heuristic" | "llm" | "fallback"
    missing_required: list[str]


class CoercedPayload(BaseModel):
    """Wrapper schema for the LLM coercion call (free-form payload inside)."""

    payload: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


def required_keys(input_schema: dict[str, Any] | None) -> list[str]:
    """Return the JSON-Schema 'required' list, or empty when schema is empty/missing."""
    if not isinstance(input_schema, dict):
        return []
    req = input_schema.get("required")
    if isinstance(req, list):
        return [str(r) for r in req]
    return []


def schema_properties(input_schema: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(input_schema, dict):
        return {}
    props = input_schema.get("properties")
    if isinstance(props, dict):
        return {str(k): (v if isinstance(v, dict) else {}) for k, v in props.items()}
    return {}


def missing_required(payload: dict[str, Any], input_schema: dict[str, Any] | None) -> list[str]:
    """Return required keys that are absent or None in payload."""
    missing: list[str] = []
    for key in required_keys(input_schema):
        if key not in payload or payload[key] is None:
            missing.append(key)
    return missing


def heuristic_coerce(user_msg: str, input_schema: dict[str, Any] | None) -> dict[str, Any] | None:
    """Try a name-match: if the schema declares a single string-shaped 'message'-ish key,
    drop the user_msg there. Returns None when no clear single mapping is possible.
    """
    props = schema_properties(input_schema)
    if not props:
        return None
    string_keys = [
        k for k, spec in props.items()
        if (spec or {}).get("type", "string") == "string"
    ]
    if not string_keys:
        return None
    # Prefer canonical names
    for canon in _HEURISTIC_KEYS:
        if canon in string_keys:
            payload = {canon: user_msg}
            # Fill any other required keys with empty strings so the agent gets *something*
            for r in required_keys(input_schema):
                payload.setdefault(r, "")
            return payload
    # Single string key — use it
    if len(string_keys) == 1:
        return {string_keys[0]: user_msg}
    return None


_COERCE_SYSTEM = (
    "你是输入转换器。给你一段用户原话和一个目标 JSON Schema,"
    "你要返回一个 JSON 对象,作为这个 schema 的 instance。\n\n"
    "规则:\n"
    "1. 只输出 {\"payload\": {...}} 这一个 JSON 对象,不要多余文字\n"
    "2. payload 内必须包含 schema 中所有 required 字段\n"
    "3. 如果用户原话不能完全提供某个字段,给最合理的最简默认值(空字符串、空数组、null)\n"
    "4. 不要编造 schema 中没有的字段\n"
)


async def llm_coerce(
    llm: LLMClient,
    *,
    model: str,
    user_msg: str,
    input_schema: dict[str, Any],
    max_tokens: int = 600,
) -> dict[str, Any] | None:
    """Single light LLM call to coerce user_msg into a payload matching input_schema."""
    user_prompt = (
        f"目标 JSON Schema:\n{json.dumps(input_schema, ensure_ascii=False, indent=2)}\n\n"
        f"用户原话:\n{user_msg}\n\n"
        "请输出 {\"payload\": {...}} 形式的 JSON。"
    )
    try:
        reply = await llm.chat(
            model,
            [
                ChatMessage(role="system", content=_COERCE_SYSTEM),
                ChatMessage(role="user", content=user_prompt),
            ],
            temperature=0.1,
            max_tokens=max_tokens,
        )
    except Exception as exc:  # noqa: BLE001
        emit("L3", "AgentInputCoercer", "llm_failed",
             f"{type(exc).__name__}: {exc}")
        return None
    text = reply.content.strip()
    # Strip markdown fences if model added them
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith(("json\n", "json\r")):
            text = text.split("\n", 1)[1] if "\n" in text else ""
    try:
        parsed = json.loads(text)
    except Exception:
        emit("L3", "AgentInputCoercer", "llm_unparseable", text[:160])
        return None
    if isinstance(parsed, dict) and isinstance(parsed.get("payload"), dict):
        return parsed["payload"]
    if isinstance(parsed, dict):
        # Some models drop the 'payload' wrapper — accept the top-level dict
        return parsed
    return None


async def coerce_inputs(
    user_msg: str,
    input_schema: dict[str, Any] | None,
    *,
    llm: LLMClient | None = None,
    model: str | None = None,
) -> CoercionResult:
    """Build inputs for an agent invocation given the user's message + agent's schema.

    Order:
      1. Schema empty / not a dict → {"message": user_msg} (method='empty_schema')
      2. Heuristic name-match → fill (method='heuristic') if it covers required keys
      3. LLM coercion → (method='llm') if llm + model provided
      4. Final fallback → {"message": user_msg, ...required_with_empties} (method='fallback')
    """
    if not isinstance(input_schema, dict) or not input_schema:
        return CoercionResult(
            inputs={"message": user_msg},
            method="empty_schema",
            missing_required=[],
        )

    heur = heuristic_coerce(user_msg, input_schema)
    if heur is not None and not missing_required(heur, input_schema):
        return CoercionResult(inputs=heur, method="heuristic", missing_required=[])

    if llm is not None and model:
        coerced = await llm_coerce(llm, model=model, user_msg=user_msg, input_schema=input_schema)
        if coerced is not None:
            miss = missing_required(coerced, input_schema)
            if not miss:
                return CoercionResult(inputs=coerced, method="llm", missing_required=[])
            # LLM produced a partial payload — return it but mark missing
            return CoercionResult(inputs=coerced, method="llm", missing_required=miss)

    # Final fallback: stub the required keys
    fallback: dict[str, Any] = {"message": user_msg}
    for r in required_keys(input_schema):
        fallback.setdefault(r, "")
    return CoercionResult(
        inputs=fallback,
        method="fallback",
        missing_required=missing_required(fallback, input_schema),
    )


__all__ = [
    "CoercionResult",
    "coerce_inputs",
    "missing_required",
    "required_keys",
    "schema_properties",
    "heuristic_coerce",
    "llm_coerce",
]
