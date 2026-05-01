from __future__ import annotations

from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "templates" / "fallback"


def load_fallback(schema: type[T]) -> T:
    """Load a minimal valid instance for the given schema."""
    template_path = TEMPLATES_DIR / f"{schema.__name__}.json"
    payload = template_path.read_text(encoding="utf-8")
    return schema.model_validate_json(payload)
