from __future__ import annotations

import json
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.core.llm.client import ChatMessage, LLMClient
from app.core.stability.fallback import load_fallback
from app.core.stability.validator import SchemaValidator
from app.core.trace.bus import emit

T = TypeVar("T", bound=BaseModel)


class RepairResult:
    """Outcome of generate_with_repair."""

    def __init__(
        self,
        value: T,
        attempts: int,
        used_fallback: bool,
        last_errors: list[str],
    ) -> None:
        self.value = value
        self.attempts = attempts
        self.used_fallback = used_fallback
        self.last_errors = last_errors


async def generate_with_repair(
    llm: LLMClient,
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    schema: type[T],
    max_repairs: int = 3,
    temperature: float = 0.2,
    max_tokens: int | None = None,
) -> RepairResult:
    """Generate output that satisfies `schema`."""
    validator = SchemaValidator(schema)
    messages = [
        ChatMessage(role="system", content=_build_system_prompt(system_prompt, schema)),
        ChatMessage(role="user", content=user_prompt),
    ]
    last_errors: list[str] = []

    emit("L4", "Repair", "start",
         f"generate_with_repair(schema={schema.__name__}, max_repairs={max_repairs})")

    for attempt in range(1, max_repairs + 1):
        emit("L4", "Repair", "attempt",
             f"attempt {attempt}/{max_repairs} · schema={schema.__name__}")
        reply = await llm.chat(model, messages, temperature=temperature, max_tokens=max_tokens)
        try:
            value = validator.parse(reply.content)
            emit("L4", "Validator", "ok",
                 f"✓ {schema.__name__} parsed (attempt {attempt})")
            return RepairResult(value=value, attempts=attempt, used_fallback=False, last_errors=last_errors)
        except ValidationError as err:
            error_summary = validator.format_error(err)
            last_errors.append(error_summary)
            emit("L4", "Validator", "fail",
                 f"✗ attempt {attempt} failed: {error_summary[:120]}")
            if attempt >= max_repairs:
                fallback = load_fallback(schema)
                emit("L4", "Fallback", "used",
                     f"降级到兜底模板 {schema.__name__}.json (after {attempt} attempts)")
                return RepairResult(
                    value=fallback,
                    attempts=attempt,
                    used_fallback=True,
                    last_errors=last_errors,
                )
            messages.append(ChatMessage(role="assistant", content=reply.content))
            messages.append(
                ChatMessage(
                    role="user",
                    content=(
                        "Previous output failed validation. Fix these issues and respond "
                        f"with JSON only:\n{error_summary}"
                    ),
                )
            )
            emit("L4", "Repair", "retry", f"feeding error back → retry {attempt + 1}")

    fallback = load_fallback(schema)
    emit("L4", "Fallback", "used",
         f"降级到兜底模板 {schema.__name__}.json (exhausted)")
    return RepairResult(value=fallback, attempts=max_repairs, used_fallback=True, last_errors=last_errors)


def _build_system_prompt(system_prompt: str, schema: type[T]) -> str:
    schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False, indent=2)
    parts = [system_prompt.strip(), f"Return JSON matching this schema:\n{schema_json}"]
    examples = schema.model_json_schema().get("examples")
    if examples:
        examples_json = json.dumps(examples[:3], ensure_ascii=False, indent=2)
        parts.append(f"Examples:\n{examples_json}")
    return "\n\n".join(part for part in parts if part)
