"""PresetBundle — front-end base modules (Batch C++).

Every generated front-end project gets these auto-injected (i18n, theme switch,
Ruidong config drawer, error boundary, ...). The LLM does NOT decide whether to
include them — the runtime injects them deterministically after Phase 3.

Why: blueprint §3.1 "deterministic shell, probabilistic core". UI base
features must not depend on prompt obedience.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.product_types import UI_PRODUCT_TYPES, ProductType

PresetFramework = Literal["react-vite", "next", "vanilla"]
PresetActivation = Literal["draft", "active", "deprecated"]


class PresetFile(BaseModel):
    """One file the preset wants to drop into the generated project."""

    path: str = Field(..., description="Target path inside the generated project.")
    template_path: str = Field(
        ..., description="Source template path under the preset root (relative)."
    )
    is_template: bool = Field(
        default=True,
        description="Whether the source is a Jinja2 template (vs. plain copy).",
    )
    overwrite: bool = Field(
        default=False,
        description="If False, an LLM-produced file at the same path wins.",
    )

    model_config = ConfigDict(extra="forbid")


class PresetEntryInjection(BaseModel):
    """Wiring instructions for a project's entry file (e.g. src/App.tsx).

    The injector applies these in priority order. ``import_lines`` are appended
    near other imports; ``provider_wrap`` wraps the root component; ``body_inject``
    is appended inside the root component's JSX (before its closing tag).
    """

    file: str = Field(..., description="Project entry file, e.g. 'src/App.tsx'.")
    import_lines: list[str] = Field(default_factory=list)
    provider_open: str | None = Field(
        default=None, description="JSX opening tag, e.g. '<I18nProvider>'."
    )
    provider_close: str | None = Field(
        default=None, description="JSX closing tag, e.g. '</I18nProvider>'."
    )
    body_inject: str | None = Field(
        default=None, description="JSX snippet inserted into the root component body."
    )

    model_config = ConfigDict(extra="forbid")


class PresetBundle(BaseModel):
    """A standardized front-end module the runtime injects into every UI project."""

    bundle_id: str = Field(..., description="Stable id, e.g. 'i18n', 'theme', 'settings_drawer'.")
    version: str = Field(default="0.1.0")
    name: str
    description: str
    framework: PresetFramework = "react-vite"
    auto_inject: bool = Field(
        default=True,
        description="If True, included by default in every UI workflow output.",
    )
    applies_to: list[ProductType] = Field(
        default_factory=lambda: list(UI_PRODUCT_TYPES),
        description="ProductTypes this preset applies to. PresetInjector skips presets whose applies_to does not contain spec.product_type.",
    )
    files: list[PresetFile] = Field(default_factory=list)
    required_deps: dict[str, str] = Field(default_factory=dict)
    required_dev_deps: dict[str, str] = Field(default_factory=dict)
    entry_injections: list[PresetEntryInjection] = Field(default_factory=list)
    provides_features: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(
        default_factory=list,
        description="Other bundle_ids that must be injected before this one.",
    )
    activation_status: PresetActivation = "active"

    model_config = ConfigDict(extra="forbid")
