from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.stability.examples import (
    BRAND_TOKENS_EXAMPLES,
    CODE_ARTIFACT_EXAMPLES,
    FILE_ENTRY_EXAMPLES,
    PAGE_BLUEPRINT_EXAMPLES,
    REQUIREMENT_SPEC_EXAMPLES,
    UI_BLUEPRINT_EXAMPLES,
)
from app.product_types import ProductType

HEX_COLOR_PATTERN = r"^#[0-9a-fA-F]{6}$"


class RequirementSpec(BaseModel):
    """L1: Structured user requirements after clarification.

    ``product_type`` drives downstream workflow branching and PresetBundle
    selection (e.g. ``app`` → auth + api_client preset injected; ``tool`` →
    no UI presets at all).
    """

    product_name: str = Field(..., max_length=32)
    product_type: ProductType = Field(
        default="app",
        description="What the user is building. Drives workflow branch and preset selection.",
    )
    target_users: list[str] = Field(..., min_length=1)
    core_pages: list[str] = Field(..., min_length=1)
    reference_brands: list[str] = Field(default_factory=list)
    special_requirements: list[str] = Field(default_factory=list)
    type_specific: dict[str, object] = Field(
        default_factory=dict,
        description="Type-specific extension slot. For app: {auth_required, roles, data_models, api_endpoints}. For tool: {function_signature, side_effects}. Etc.",
    )

    model_config = ConfigDict(json_schema_extra={"examples": REQUIREMENT_SPEC_EXAMPLES})


class PageBlueprint(BaseModel):
    """A page-level UI blueprint."""

    route: str
    title: str
    components: list[Literal["hero", "list", "form", "card-grid", "table"]]
    data_fields: list[str]

    model_config = ConfigDict(json_schema_extra={"examples": PAGE_BLUEPRINT_EXAMPLES})


class BrandTokens(BaseModel):
    """Brand tokens for generated UI."""

    primary: str = Field(..., pattern=HEX_COLOR_PATTERN)
    font: str
    radius: Literal["none", "sm", "md", "lg"]

    model_config = ConfigDict(json_schema_extra={"examples": BRAND_TOKENS_EXAMPLES})


class UIBlueprint(BaseModel):
    """L2: RequirementSpec to UI blueprint."""

    pages: list[PageBlueprint] = Field(..., min_length=1)
    brand: BrandTokens

    model_config = ConfigDict(json_schema_extra={"examples": UI_BLUEPRINT_EXAMPLES})


class FileEntry(BaseModel):
    """A generated file payload."""

    path: str
    content: str

    model_config = ConfigDict(json_schema_extra={"examples": FILE_ENTRY_EXAMPLES})


class CodeArtifact(BaseModel):
    """L3: Deployable generated output."""

    files: list[FileEntry] = Field(..., min_length=1)
    dependencies: dict[str, str] = Field(default_factory=dict)
    entrypoint: str

    model_config = ConfigDict(json_schema_extra={"examples": CODE_ARTIFACT_EXAMPLES})
