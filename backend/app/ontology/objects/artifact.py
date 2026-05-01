"""Artifact — blueprint §8.6.

Every produced/consumed blob crossing a workflow boundary is an Artifact.
Content itself lives in the object store; Artifact is the typed handle.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ArtifactType = Literal[
    "input_payload",
    "requirement_spec",
    "ui_blueprint",
    "prototype_bundle",
    "code_artifact",
    "plan",
    "verification_report",
    "policy_decision",
    "eval_result",
    "log_bundle",
    "zip_package",
    "custom",
]


class Artifact(BaseModel):
    """Typed handle to a stored blob (blueprint §8.6)."""

    artifact_id: str = Field(..., description="UUID hex, globally unique.")
    artifact_type: ArtifactType = Field(..., description="Declared role of this blob.")
    mime_type: str = Field(default="application/json")
    storage_uri: str = Field(..., description="Object-store URI or file:// path.")
    hash: str = Field(default="", description="sha256 of the content bytes.")
    size_bytes: int = Field(default=0)
    schema_version: str | None = Field(
        default=None, description="Version of the Ontology schema this artifact conforms to."
    )
    producer_run_id: str | None = None
    producer_step_id: str | None = None
    parent_artifact_ids: list[str] = Field(
        default_factory=list,
        description="Upstream artifacts this one was derived from (lineage).",
    )
    created_at: datetime | None = None
    metadata: dict[str, object] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")
