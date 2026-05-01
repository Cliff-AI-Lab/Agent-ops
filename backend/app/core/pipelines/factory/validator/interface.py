"""DSLValidator Protocol."""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel


class ValidationIssue(BaseModel):
    severity: Literal["error", "warning", "info"]
    node_id: str | None = None
    message: str
    fix_hint: str | None = None


class ValidationReport(BaseModel):
    ok: bool
    target: Literal["dify", "n8n", "hybrid"]
    issues: list[ValidationIssue] = []


class DSLValidator(Protocol):
    """Validate compiled DSL: schema + static + dry-run."""

    async def validate(self, dsl: str, target: str) -> ValidationReport: ...
