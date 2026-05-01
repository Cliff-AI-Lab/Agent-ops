"""DSLValidator implementation.

Phase 1 W3: Schema check only. Dry-run + LLM repair come in Phase 2.
"""

from __future__ import annotations

from app.core.pipelines.factory.validator.interface import (
    DSLValidator,
    ValidationReport,
)


class DSLValidatorImpl:
    async def validate(self, dsl: str, target: str) -> ValidationReport:
        raise NotImplementedError(
            "DSLValidatorImpl.validate() will be implemented in Phase 1 W3 day 3-4."
        )
