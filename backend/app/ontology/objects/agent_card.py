"""AgentCard — blueprint §8.2.

An AgentCard is a self-description published by each agent. Used by the Registry
and the A2A protocol for discovery. Aligned with Google A2A "Agent Cards".
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

TransportType = Literal["local", "http", "mcp", "a2a", "cli"]
AuthMode = Literal["none", "bearer", "oauth", "mtls", "custom"]
CostProfile = Literal["cheap", "standard", "premium", "custom"]
HealthStatus = Literal["healthy", "degraded", "unhealthy", "unknown"]


class AgentCard(BaseModel):
    """Self-description of a concrete agent endpoint (blueprint §8.2)."""

    agent_id: str = Field(..., description="Globally unique id (namespaced, e.g. 'raytone.ui-agent').")
    version: str = Field(..., description="Semver.")
    display_name: str
    description: str
    capabilities: list[str] = Field(
        default_factory=list,
        description="capability_ids this agent implements (references CapabilityContract).",
    )
    transport_type: TransportType = Field(..., description="How the runtime reaches this agent.")
    endpoint: str | None = Field(
        default=None,
        description="URL for http/mcp/a2a agents; null for local.",
    )
    auth_mode: AuthMode = Field(default="none")
    supports_streaming: bool = Field(default=False)
    supports_human_handoff: bool = Field(default=False)
    expected_latency_ms: int | None = Field(default=None)
    cost_profile: CostProfile = Field(default="standard")
    health_status: HealthStatus = Field(default="unknown")
    tags: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")
