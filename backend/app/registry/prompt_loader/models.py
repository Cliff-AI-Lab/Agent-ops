"""PromptDef Pydantic models — prompt as first-class versioned asset.

Per [[资产中心/总览]] § 9 货架 #2 (Prompts). Prompts are referenced by
ResolvedNode.prompt_id (e.g. 'prompt.report.zh_writer.v1') and rendered
at runtime by the LLM atom.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


_ASSET_ID_PATTERN = re.compile(r"^prompt\.[a-z_]+(\.[a-z_]+)*\.v\d+$")


class PromptInput(BaseModel):
    """A template variable expected by the prompt."""

    name: str
    type: str = Field(description="x-data-type label (tabular_data, json_object, ...)")
    required: bool = True
    description: str | None = None


class PromptOutputSchema(BaseModel):
    """Expected LLM output shape; used as response_format hint."""

    format: Literal["text", "json_object", "markdown_text"] = "text"
    schema_hint: dict[str, Any] | None = None


class PromptTestCase(BaseModel):
    name: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    expected_categories: list[str] | None = None
    expected_min_chars: int | None = None
    expected_contains: list[str] | None = None


class PromptMetrics(BaseModel):
    golden_set_pass_rate: float | None = None
    p95_latency_ms: int | None = None
    avg_tokens: int | None = None


class PromptProvenance(BaseModel):
    built_at: str
    built_by: str = "manual"
    source_url: str | None = None
    license: str | None = None


class PromptDef(BaseModel):
    """Top-level prompt asset definition. One YAML file -> one PromptDef."""

    schema_version: Literal["1.0"]
    asset_id: str
    display_id: str | None = None
    type: Literal["prompt"] = "prompt"
    version: str

    name: str
    description: str = Field(min_length=15)
    tags: list[str] = Field(min_length=1)

    # Compatibility hints (not enforcing): which task_type+size this prompt
    # was tuned for, per [[资产中心/横切-LLM模型路由表]].
    linked_task_type: str | None = None
    linked_size: Literal["小", "中", "大"] | None = None

    inputs: list[PromptInput] = Field(default_factory=list)
    output: PromptOutputSchema = Field(default_factory=PromptOutputSchema)

    template: str = Field(
        min_length=20,
        description="Jinja2 template; variables referenced as {{name}}.",
    )

    test_cases: list[PromptTestCase] = Field(min_length=1)
    metrics: PromptMetrics = Field(default_factory=PromptMetrics)
    maintainer: str
    provenance: PromptProvenance

    @field_validator("asset_id")
    @classmethod
    def _validate_asset_id(cls, v: str) -> str:
        if not _ASSET_ID_PATTERN.match(v):
            raise ValueError(
                f"asset_id must match {_ASSET_ID_PATTERN.pattern}, got {v!r}"
            )
        return v
