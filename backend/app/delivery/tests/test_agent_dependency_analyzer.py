"""Phase 10 W2 - tests for AgentDependencyAnalyzer."""

from __future__ import annotations

import pytest

from app.core.pipelines.factory.ir.multi_agent import (
    GuardrailSpec,
    HandoffEdge,
    IndustryTag,
    MultiAgentSpec,
    SpecialistSpec,
    TriageSpec,
)
from app.delivery.agent_dependency_analyzer import (
    AgentDependencyAnalyzer,
    AgentImpactReport,
    ToolOverlap,
)


def _spec(
    *,
    triage_targets: list[str] | None = None,
    specialists: list[dict] | None = None,
    handoffs: list[dict] | None = None,
) -> MultiAgentSpec:
    return MultiAgentSpec(
        name="airline customer service",
        description="multi-agent system for airline customer service",
        industry=IndustryTag(code="05", primary="交通", sub="航空"),
        triage=TriageSpec(
            name="Triage",
            system_prompt_id="prompt.triage.airline.v1",
            initial_handoff_targets=triage_targets or [],
        ),
        specialists=[
            SpecialistSpec(
                id=s["id"],
                name=s.get("name", s["id"]),
                agent_class=s.get("agent_class", "customer_service"),
                description=s.get("description", "specialist agent role"),
                nl_brief=s.get("nl_brief", "specialist nl brief description"),
                handoff_targets=s.get("handoff_targets", []),
                tools=s.get("tools", []),
            )
            for s in (specialists or [])
        ],
        handoffs=[
            HandoffEdge(
                from_specialist=h["from"],
                to_specialist=h["to"],
                when=h.get("when", "trigger condition"),
            )
            for h in (handoffs or [])
        ],
        guardrails=[GuardrailSpec(kind="relevance", description="block off-topic queries")],
    )


def test_unknown_agent_returns_no_impact():
    spec = _spec(specialists=[{"id": "booking"}])
    report = AgentDependencyAnalyzer().analyze_change(spec, "ghost")
    assert report.impact_kinds == ["no_impact"]
    assert report.affected_count == 0


def test_isolated_agent_no_impact():
    spec = _spec(
        specialists=[
            {"id": "lonely", "tools": ["atom.unique.v1"]},
            {"id": "other", "tools": ["atom.different.v1"]},
        ],
    )
    report = AgentDependencyAnalyzer().analyze_change(spec, "lonely")
    assert report.impact_kinds == ["no_impact"]


def test_handoff_chain_direct_upstream():
    spec = _spec(
        triage_targets=["booking"],
        specialists=[
            {"id": "booking", "handoff_targets": ["seat", "refund"]},
            {"id": "seat"},
            {"id": "refund"},
        ],
    )
    r = AgentDependencyAnalyzer().analyze_change(spec, "seat")
    assert "booking" in r.direct_upstream
    assert "handoff_chain" in r.impact_kinds


def test_handoff_chain_direct_downstream():
    spec = _spec(
        specialists=[
            {"id": "booking", "handoff_targets": ["seat", "refund"]},
            {"id": "seat"},
            {"id": "refund"},
        ],
    )
    r = AgentDependencyAnalyzer().analyze_change(spec, "booking")
    assert sorted(r.direct_downstream) == ["refund", "seat"]


def test_triage_upstream_direction():
    """When triage hands off to an agent, that agent's upstream includes triage."""
    spec = _spec(
        triage_targets=["booking", "faq"],
        specialists=[{"id": "booking"}, {"id": "faq"}],
    )
    r = AgentDependencyAnalyzer().analyze_change(spec, "booking")
    assert "triage" in r.direct_upstream


def test_triage_downstream_direction():
    """Changing triage should show its initial_handoff_targets as downstream."""
    spec = _spec(
        triage_targets=["a", "b"],
        specialists=[{"id": "a"}, {"id": "b"}],
    )
    r = AgentDependencyAnalyzer().analyze_change(spec, "triage")
    assert sorted(r.direct_downstream) == ["a", "b"]


def test_shared_tool_overlap_detected():
    spec = _spec(
        specialists=[
            {"id": "a", "tools": ["atom.db.postgres.v1", "atom.llm.chat.v1"]},
            {"id": "b", "tools": ["atom.db.postgres.v1"]},
            {"id": "c", "tools": ["atom.unrelated.v1"]},
        ],
    )
    r = AgentDependencyAnalyzer().analyze_change(spec, "a")
    overlaps = {o.other_agent_id: o.shared_tools for o in r.tool_overlap}
    assert overlaps == {"b": ["atom.db.postgres.v1"]}
    assert "shared_tool" in r.impact_kinds


def test_transitive_reach_via_handoffs():
    spec = _spec(
        specialists=[
            {"id": "a", "handoff_targets": ["b"]},
            {"id": "b", "handoff_targets": ["c"]},
            {"id": "c"},
            {"id": "d"},  # not connected
        ],
    )
    r = AgentDependencyAnalyzer().analyze_change(spec, "a")
    assert "b" in r.transitive_reach
    assert "c" in r.transitive_reach
    assert "d" not in r.transitive_reach
    assert "transitive" in r.impact_kinds


def test_global_handoffs_picked_up():
    """spec.handoffs (global edges) are also treated as dependency edges."""
    spec = _spec(
        specialists=[{"id": "a"}, {"id": "b"}],
        handoffs=[{"from": "a", "to": "b"}],
    )
    r = AgentDependencyAnalyzer().analyze_change(spec, "a")
    assert "b" in r.direct_downstream


def test_affected_count_unique_across_dimensions():
    """Same agent showing up in multiple dimensions counts once."""
    spec = _spec(
        specialists=[
            {"id": "a", "handoff_targets": ["b"], "tools": ["atom.shared.v1"]},
            {"id": "b", "tools": ["atom.shared.v1"]},
        ],
    )
    r = AgentDependencyAnalyzer().analyze_change(spec, "a")
    # b shows up in direct_downstream + tool_overlap + transitive
    # but counts once
    assert r.affected_count == 1


def test_short_summary_renders_human_readable():
    spec = _spec(
        triage_targets=["booking"],
        specialists=[
            {"id": "booking", "handoff_targets": ["seat"], "tools": ["atom.db.v1"]},
            {"id": "seat", "tools": ["atom.db.v1"]},
        ],
    )
    r = AgentDependencyAnalyzer().analyze_change(spec, "booking")
    summary = r.short_summary()
    assert "upstream" in summary or "downstream" in summary or "shared-tool" in summary


def test_no_self_loop_in_transitive():
    """Even if an agent loops back to itself via others, it does not list itself."""
    spec = _spec(
        specialists=[
            {"id": "a", "handoff_targets": ["b"]},
            {"id": "b", "handoff_targets": ["a"]},
        ],
    )
    r = AgentDependencyAnalyzer().analyze_change(spec, "a")
    assert "a" not in r.transitive_reach
    assert "b" in r.transitive_reach


def test_changing_agent_with_no_tools_no_overlap():
    spec = _spec(
        specialists=[
            {"id": "lonely", "tools": []},
            {"id": "tooled", "tools": ["atom.x.v1"]},
        ],
    )
    r = AgentDependencyAnalyzer().analyze_change(spec, "lonely")
    assert r.tool_overlap == []
