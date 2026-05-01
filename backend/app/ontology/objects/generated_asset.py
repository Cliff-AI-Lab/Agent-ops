"""GeneratedAsset — Marketplace + Asset Hub canonical record (Batch X).

Every successful DeliverPipeline run produces one GeneratedAsset:
- the actual zip on disk(s)
- the AgentContract auto-generated next to ``agents/__generated__/{run_id}/``
- a Marketplace row that can be listed / downloaded / promoted

Once promoted, the AgentContract is loaded into the live RegistryHub and the
IntentRouter retrieves it side-by-side with platform-native capabilities.
This is the **self-reinforcing loop**:
"今天的 agent 是明天的原子"。
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.product_types import ProductType

AssetStatus = Literal[
    "draft",       # auto-registered, not searchable in RegistryHub yet
    "active",      # promoted by user → indexed for IntentRouter retrieval
    "archived",    # withdrawn from Wiki / discovery
]


class GeneratedAsset(BaseModel):
    """One row in the asset_hub table — the audit + reuse record of one Generation."""

    asset_id: str = Field(..., description="Stable id, typically equals the run_id (12-char hex).")
    name: str
    description: str = ""
    product_type: ProductType = "app"

    # Provenance
    source_session_id: str | None = None
    source_sop: str | None = Field(default=None, description="The original one-sentence SOP.")
    generation_id: str | None = None
    generated_at: datetime = Field(default_factory=datetime.utcnow)

    # Files on disk
    artifact_path: str = Field(..., description="Relative path to the project zip (e.g. 'assets/{run_id}/project.zip').")
    agent_yaml_path: str | None = Field(
        default=None,
        description="Relative path to the auto-generated agent.yaml under agents/__generated__/.",
    )
    file_count: int = 0
    total_bytes: int = 0

    # Discoverability
    intent_keywords: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    capabilities_used: list[str] = Field(default_factory=list)
    tools_used: list[str] = Field(default_factory=list)

    # Quality / lifecycle
    quality_score: float | None = Field(default=None, ge=0.0, le=1.0)
    usage_count: int = 0
    promoted_by: str | None = None
    promoted_at: datetime | None = None
    status: AssetStatus = "draft"

    model_config = ConfigDict(extra="forbid")
