"""ResolvedDAG: IR2 produced by Resolver.

Atom-bound workflow graph with type-checked edges.
Input to DSLCompiler.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class TypeCheck(BaseModel):
    """Type contract on an edge between two nodes (v2 audit gap #1)."""

    from_type: str = Field(description="Output data type, e.g. 'tabular_data'")
    to_type: str = Field(description="Input data type expected by downstream")


class ResolvedNode(BaseModel):
    """A node in the resolved DAG with a specific atom bound."""

    id: str = Field(description="Aligns with StructuredIntent.steps[].id")
    asset_id: str = Field(description="e.g. 'atom.db.postgres.v1'")
    asset_version: str = Field(description="locked semver, e.g. '1.0.0'")
    confidence: float = Field(ge=0.0, le=1.0)
    selection_reason: str = Field(
        min_length=1,
        description="LLM-given reason for selection. MUST be non-empty for trace.",
    )
    inputs: dict[str, Any] = Field(default_factory=dict)
    params: dict[str, Any] = Field(default_factory=dict)

    # LLM-only fields (atom.llm.* nodes)
    llm_task_type: str | None = None
    llm_size: Literal["小", "中", "大"] | None = None
    llm_fallback_chain: list[Literal["小", "中", "大"]] = Field(default_factory=list)

    # Prompt reference (never inline prompt text)
    prompt_id: str | None = Field(
        default=None,
        description="e.g. 'prompt.report.zh_writer.v1'",
    )


class ResolvedEdge(BaseModel):
    """An edge between two nodes in the resolved DAG."""

    from_node: str
    to_node: str
    from_var: str = Field(description="Upstream output var name")
    to_var: str = Field(description="Downstream input var name")
    type_check: TypeCheck
    needs_adapter: bool = False
    adapter_combo: str | None = Field(
        default=None,
        description="If needs_adapter, the combo to insert, e.g. 'combo.type_adapter.json_to_table.v1'",
    )


class ResolvedDAG(BaseModel):
    """Final output of Resolver. Input to DSLCompiler."""

    schema_version: Literal["1.0"] = "1.0"
    intent_ref: str = Field(description="Reference to source Intent ID")
    pattern_id: str | None = Field(
        default=None,
        description="Matched L3 combo, e.g. 'combo.cron_etl_report.v1'",
    )
    nodes: list[ResolvedNode]
    edges: list[ResolvedEdge]
    target: Literal["dify", "n8n", "hybrid"]
    target_split: dict[str, list[str]] | None = Field(
        default=None,
        description="For hybrid: {'n8n_nodes': [...], 'dify_nodes': [...]}",
    )
    estimated_cost_cny: float | None = None
    estimated_latency_ms: int | None = None
    issues: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Resolver-detected gaps or low-confidence selections",
    )
