"""EvalCase / EvalReport models.

Loose-check style (Phase 1 W2): structural assertions only, NOT LLM output match.
What we check:
  - factory.build succeeded (no exception)
  - target == expected_target (dify/n8n/hybrid)
  - expected_subcategories ⊆ resolved subcategories
  - node count within range
  - validator ok (or accepted issues)

What we DON'T check (yet):
  - exact step verbs (LLM varies)
  - exact prompt content
  - exact YAML output (Phase 4 will add snapshot tests)
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ExpectedShape(BaseModel):
    """Loose structural assertions for an eval case."""

    target: Literal["dify", "n8n", "hybrid"] | None = None
    subcategories: list[str] = Field(
        default_factory=list,
        description="Expected subcategories present in resolved DAG (order-free, subset)",
    )
    min_nodes: int | None = None
    max_nodes: int | None = None
    min_edges: int | None = None
    require_validator_ok: bool = True
    allowed_issue_kinds: list[str] = Field(
        default_factory=lambda: ["type_adapters_needed"],
        description="Issue kinds allowed without failing the case",
    )
    # ---- V2.1.0 multi-agent fields (Phase 7) ----
    # These are honored by MultiAgentEvalRunner; ignored by single-agent runner.
    classify_industry: str | None = Field(
        default=None, description="Expected IndustryClassification.industry_code"
    )
    classify_scenario: str | None = Field(
        default=None, description="Expected business_scenario substring match"
    )
    classify_is_multi_agent: bool | None = Field(
        default=None, description="Expected IndustryClassification.is_multi_agent"
    )
    min_specialists: int | None = Field(
        default=None, description="Multi-agent: minimum specialist count"
    )
    max_specialists: int | None = Field(
        default=None, description="Multi-agent: maximum specialist count"
    )
    min_handoffs: int | None = Field(
        default=None, description="Multi-agent: minimum handoff edge count"
    )
    require_compose_ok: bool = Field(
        default=False,
        description="Multi-agent: Composer must produce ast.parse-able Python",
    )


class EvalCase(BaseModel):
    """One natural-language test case."""

    case_id: str
    description: str
    nl: str = Field(min_length=5)
    expected: ExpectedShape
    tags: list[str] = Field(default_factory=list)


class EvalSet(BaseModel):
    """A collection of eval cases."""

    asset_id: str
    version: str
    description: str
    cases: list[EvalCase] = Field(min_length=1)
    maintainer: str = "TBD / 2026-05"


class EvalCaseResult(BaseModel):
    """Outcome of running ONE case through the factory."""

    case_id: str
    passed: bool
    reasons: list[str] = Field(default_factory=list)
    actual_target: str | None = None
    actual_subcategories: list[str] = Field(default_factory=list)
    actual_node_count: int = 0
    actual_edge_count: int = 0
    actual_issues: list[dict] = Field(default_factory=list)
    elapsed_ms: int = 0
    error: str | None = None
    # ---- V2.1.0 multi-agent fields ----
    actual_industry: str | None = None
    actual_scenario: str | None = None
    actual_is_multi_agent: bool | None = None
    actual_specialists: int | None = None
    actual_handoffs: int | None = None
    compose_ok: bool | None = None


class EvalReport(BaseModel):
    """Full report of an eval set run."""

    eval_set_id: str
    total: int
    passed: int
    failed: int
    pass_rate: float
    case_results: list[EvalCaseResult] = Field(default_factory=list)

    @property
    def is_mvp_threshold_met(self) -> bool:
        """v2 audit: MVP gate at 70% (Phase 4 § 一)."""
        return self.pass_rate >= 0.70

    @property
    def is_ga_threshold_met(self) -> bool:
        return self.pass_rate >= 0.80
