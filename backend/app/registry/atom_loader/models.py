"""AtomDef Pydantic models.

See E:/Obsidian/Agent 工厂/工程规范/原子YAML规范.md § 二
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


_ASSET_ID_PATTERN = re.compile(r"^atom\.[a-z]+\.[a-z_]+\.v\d+$")


class IOSchema(BaseModel):
    inputs: dict[str, Any]
    outputs: dict[str, Any]


class ProjectionDef(BaseModel):
    node_type: str | None = None
    template: str | None = None
    compatible_dify_version: str | None = None
    compatible_n8n_version: str | None = None
    requires_adapter: bool = False
    not_supported: bool = False
    note: str | None = None


class TestCase(BaseModel):
    name: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    expected_output_match: dict[str, Any] | None = None
    expected_error_class: str | None = None


class HealthCheck(BaseModel):
    fixture_path: str | None = None
    schedule: str = "every_24h"
    status: Literal["green", "yellow", "red"] = "green"
    last_check_at: str | None = None


class Metrics(BaseModel):
    pass_rate: float | None = None
    p95_latency_ms: int | None = None
    cost_per_call_cny: float | None = 0.0


class Provenance(BaseModel):
    built_at: str
    built_by: str
    source_url: str | None = None
    license: str | None = None


class ModelRouting(BaseModel):
    """LLM-only field. Atoms with subcategory == 'LLM' must have this."""

    required: bool = True
    task_types_supported: list[str] = Field(default_factory=list)
    default_fallback_chain: dict[str, list[str]] = Field(default_factory=dict)


class AtomDef(BaseModel):
    """Top-level atom definition. One YAML file -> one AtomDef."""

    schema_version: Literal["1.0"]
    asset_id: str
    display_id: str | None = None
    type: Literal["atom"] = "atom"
    layer: Literal["L2"] = "L2"
    subcategory: str
    version: str

    name: str
    name_en: str | None = None
    description: str = Field(min_length=20)
    tags: list[str] = Field(min_length=1)

    recommend: str | None = None
    adoption_cost: Literal["低", "中", "高"] | None = None
    applicable_scenarios: list[str] = Field(default_factory=list)
    NOT_applicable: list[str] = Field(min_length=1)
    price_summary: str | None = None
    case_projects: list[str] = Field(default_factory=list)
    maintainer: str
    notes_url: str | None = None

    io_schema: IOSchema
    projections: dict[str, ProjectionDef] = Field(min_length=1)
    model_routing: ModelRouting | None = None

    health_check: HealthCheck | None = None
    test_cases: list[TestCase] = Field(min_length=2)
    metrics: Metrics = Field(default_factory=Metrics)
    provenance: Provenance

    @field_validator("asset_id")
    @classmethod
    def _validate_asset_id(cls, v: str) -> str:
        if not _ASSET_ID_PATTERN.match(v):
            raise ValueError(
                f"asset_id must match {_ASSET_ID_PATTERN.pattern}, got {v!r}"
            )
        return v
