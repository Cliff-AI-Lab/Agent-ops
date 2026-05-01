from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


class SchemaValidator:
    """Validates raw LLM text output against a Pydantic schema."""

    def __init__(self, schema: type[T]) -> None:
        self._schema = schema

    def parse(self, raw: str) -> T:
        """Parse raw text. Tries JSON first; raises ValidationError on failure."""
        candidate = _extract_json_candidate(raw)
        return self._schema.model_validate_json(candidate)

    def format_error(self, err: ValidationError) -> str:
        """Render ValidationError into a short, model-digestible feedback string."""
        parts: list[str] = []
        for item in err.errors():
            path = _format_path(item.get("loc", ()))
            message = str(item.get("msg", "invalid value"))
            parts.append(f"{path}: {message}" if path else message)
        summary = "; ".join(parts) or "Invalid JSON output."
        if len(summary) <= 400:
            return summary
        return f"{summary[:397].rstrip()}..."


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


def _format_path(loc: tuple[object, ...]) -> str:
    if not loc:
        return ""
    segments: list[str] = []
    for item in loc:
        if isinstance(item, int):
            if segments:
                segments[-1] = f"{segments[-1]}[{item}]"
            else:
                segments.append(f"[{item}]")
            continue
        segments.append(str(item))
    return ".".join(segment for segment in segments if segment)
