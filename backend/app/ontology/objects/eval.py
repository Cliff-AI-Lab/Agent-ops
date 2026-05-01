"""EvalCase / EvalResult — blueprint M1 + M8.

Golden fixtures and scored outcomes. Placeholder shape; will be extended in
Batch G (Eval loop).
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

EvalOutcome = Literal["pass", "fail", "partial", "skipped"]


class EvalCase(BaseModel):
    """A golden-fixture test case for a capability or workflow."""

    eval_case_id: str
    suite_id: str
    capability_id: str | None = None
    workflow_id: str | None = None
    input_ref: str = Field(..., description="Artifact ref of the fixture input.")
    expected_schema: str | None = None
    expected_output_ref: str | None = None
    quality_checks: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class EvalResult(BaseModel):
    """The scored outcome of running one EvalCase."""

    eval_result_id: str
    eval_case_id: str
    outcome: EvalOutcome
    scores: dict[str, float] = Field(default_factory=dict)
    run_id: str | None = None
    executed_at: datetime | None = None
    notes: str = ""

    model_config = ConfigDict(extra="forbid")
