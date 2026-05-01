"""ToolContract — external tool declaration for the Tool Wiki.

A ToolContract describes an external, discoverable tool (OCR / TTS / ASR /
web-scrape / RAG / report-analysis / ...) that agents and workflows can plug
into via the L5/L6 Adapter layer (blueprint M5).

This is the "货架" (shelf) that the Planner browses to pick components.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ToolCategory = Literal[
    "ocr",
    "speech_tts",
    "speech_asr",
    "vision",
    "image_generation",
    "web_scrape",
    "web_search",
    "rag",
    "report_analysis",
    "translation",
    "data_transform",
    "database",
    "notification",
    "calendar",
    "crm",
    "custom",
]

ToolTransport = Literal["local", "http", "mcp", "a2a", "cli"]
ToolAuthMode = Literal["none", "api_key", "oauth", "bearer", "mtls", "custom"]
PricingModel = Literal["free", "per_call", "per_unit", "subscription", "license"]
ToolActivation = Literal["draft", "active", "deprecated", "archived"]


class ToolExample(BaseModel):
    """A runnable example illustrating intended use."""

    title: str
    input: dict[str, object] = Field(default_factory=dict)
    output_hint: str = Field(default="", description="Free-form text summary of expected output.")

    model_config = ConfigDict(extra="forbid")


class ToolContract(BaseModel):
    """Declaration of one external tool in the Tool Wiki."""

    tool_id: str = Field(..., description="Stable id, e.g. 'baidu.ocr.general_accurate'.")
    version: str = Field(default="0.1.0")
    name: str
    description: str
    provider: str = Field(..., description="e.g. 'Baidu AI' / 'OpenAI' / 'Internal'.")
    category: ToolCategory
    tags: list[str] = Field(default_factory=list)
    intent_keywords: list[str] = Field(
        default_factory=list,
        description="Natural-language intent triggers used by IntentRouter to retrieve this tool. Free-form 中/英 phrases (e.g. '识别身份证', 'OCR 发票', 'extract text from image').",
    )

    # I/O contract
    input_schema: dict[str, object] = Field(default_factory=dict)
    output_schema: dict[str, object] = Field(default_factory=dict)

    # Transport & auth (realized by adapters in Batch F)
    transport_type: ToolTransport = "http"
    endpoint: str | None = None
    auth_mode: ToolAuthMode = "api_key"
    auth_env_vars: list[str] = Field(
        default_factory=list,
        description="Env var names used for credentials (never the secret itself).",
    )

    # Operational metadata
    pricing_model: PricingModel = "free"
    cost_per_call: float | None = None
    rate_limit_per_minute: int | None = None
    expected_latency_ms: int | None = None

    # Discoverability
    docs_url: str | None = None
    examples: list[ToolExample] = Field(default_factory=list)

    activation_status: ToolActivation = "draft"

    model_config = ConfigDict(extra="forbid")
