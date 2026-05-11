"""Phase 10 W2 D3 - tests for MultiAgentSpecDiffer."""

from __future__ import annotations

import pytest

from app.core.pipelines.factory.ir.multi_agent import (
    GuardrailSpec,
    HandoffEdge,
    IndustryTag,
    MultiAgentSpec,
    SharedContextField,
    SpecialistSpec,
    TriageSpec,
)
from app.delivery.multi_agent_spec_differ import (
    MultiAgentSpecDiff,
    MultiAgentSpecDiffer,
    SpecialistDelta,
)


def _spec(
    *,
    name: str = "test_system",
    triage_targets: list[str] | None = None,
    triage_prompt: str = "prompt.triage.v1",
    specialists: list[dict] | None = None,
    handoffs: list[dict] | None = None,
    guardrails: list[dict] | None = None,
    shared: list[dict] | None = None,
) -> MultiAgentSpec:
    return MultiAgentSpec(
        name=name,
        description="x" * 25,
        industry=IndustryTag(code="01", primary="通用"),
        triage=TriageSpec(
            name="Triage",
            system_prompt_id=triage_prompt,
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
                from_specialist=h["from"], to_specialist=h["to"],
                when=h.get("when", "trigger condition"),
            )
            for h in (handoffs or [])
        ],
        guardrails=[GuardrailSpec(**g) for g in (guardrails or [])] or [],
        shared_context=[SharedContextField(**s) for s in (shared or [])],
    )


def test_identical_specs_no_change():
    a = _spec(specialists=[{"id": "x"}])
    b = _spec(specialists=[{"id": "x"}])
    diff = MultiAgentSpecDiffer().diff(a, b)
    assert diff.human_tuned is False
    assert "no spec change" in diff.short_summary()


def test_specialist_added_detected():
    a = _spec(specialists=[{"id": "x"}])
    b = _spec(specialists=[{"id": "x"}, {"id": "y"}])
    diff = MultiAgentSpecDiffer().diff(a, b)
    assert diff.specialists_added == ["y"]
    assert diff.specialists_removed == []
    assert diff.human_tuned is True


def test_specialist_removed_detected():
    a = _spec(specialists=[{"id": "x"}, {"id": "y"}])
    b = _spec(specialists=[{"id": "x"}])
    diff = MultiAgentSpecDiffer().diff(a, b)
    assert diff.specialists_removed == ["y"]


def test_handoff_target_added():
    a = _spec(specialists=[{"id": "x", "handoff_targets": ["y"]}, {"id": "y"}])
    b = _spec(specialists=[
        {"id": "x", "handoff_targets": ["y", "z"]},
        {"id": "y"},
        {"id": "z"},
    ])
    diff = MultiAgentSpecDiffer().diff(a, b)
    delta = next(d for d in diff.specialist_deltas if d.specialist_id == "x")
    assert "z" in delta.handoff_targets_added


def test_handoff_target_removed():
    a = _spec(specialists=[
        {"id": "x", "handoff_targets": ["y", "z"]},
        {"id": "y"}, {"id": "z"},
    ])
    b = _spec(specialists=[
        {"id": "x", "handoff_targets": ["y"]},
        {"id": "y"}, {"id": "z"},
    ])
    diff = MultiAgentSpecDiffer().diff(a, b)
    delta = next(d for d in diff.specialist_deltas if d.specialist_id == "x")
    assert "z" in delta.handoff_targets_removed


def test_tools_changed():
    a = _spec(specialists=[{"id": "x", "tools": ["atom.a.v1"]}])
    b = _spec(specialists=[{"id": "x", "tools": ["atom.a.v1", "atom.b.v1"]}])
    diff = MultiAgentSpecDiffer().diff(a, b)
    delta = next(d for d in diff.specialist_deltas if d.specialist_id == "x")
    assert delta.tools_added == ["atom.b.v1"]
    assert delta.tools_removed == []


def test_nl_brief_change_flagged():
    a = _spec(specialists=[{"id": "x", "nl_brief": "old description text"}])
    b = _spec(specialists=[{"id": "x", "nl_brief": "new description text changed"}])
    diff = MultiAgentSpecDiffer().diff(a, b)
    delta = next(d for d in diff.specialist_deltas if d.specialist_id == "x")
    assert delta.nl_brief_changed is True


def test_agent_class_change_flagged():
    a = _spec(specialists=[{"id": "x", "agent_class": "customer_service"}])
    b = _spec(specialists=[{"id": "x", "agent_class": "data_analytics"}])
    diff = MultiAgentSpecDiffer().diff(a, b)
    delta = next(d for d in diff.specialist_deltas if d.specialist_id == "x")
    assert delta.agent_class_changed is True


def test_triage_handoff_changes():
    a = _spec(
        triage_targets=["x"],
        specialists=[{"id": "x"}, {"id": "y"}],
    )
    b = _spec(
        triage_targets=["x", "y"],
        specialists=[{"id": "x"}, {"id": "y"}],
    )
    diff = MultiAgentSpecDiffer().diff(a, b)
    assert diff.triage_handoff_added == ["y"]
    assert diff.triage_handoff_removed == []


def test_triage_prompt_change():
    a = _spec(triage_prompt="prompt.v1", specialists=[{"id": "x"}])
    b = _spec(triage_prompt="prompt.v2", specialists=[{"id": "x"}])
    diff = MultiAgentSpecDiffer().diff(a, b)
    assert diff.triage_prompt_changed is True


def test_guardrails_changed():
    a = _spec(specialists=[{"id": "x"}],
              guardrails=[{"kind": "relevance", "description": "block off-topic queries"}])
    b = _spec(specialists=[{"id": "x"}],
              guardrails=[
                  {"kind": "relevance", "description": "block off-topic queries"},
                  {"kind": "jailbreak", "description": "block prompt injection attempts"},
              ])
    diff = MultiAgentSpecDiffer().diff(a, b)
    assert diff.guardrails_changed is True


def test_shared_context_changed():
    a = _spec(specialists=[{"id": "x"}],
              shared=[{"name": "user_id", "type": "string", "description": "user identifier"}])
    b = _spec(specialists=[{"id": "x"}],
              shared=[
                  {"name": "user_id", "type": "string", "description": "user identifier"},
                  {"name": "tier", "type": "integer", "description": "membership tier"},
              ])
    diff = MultiAgentSpecDiffer().diff(a, b)
    assert diff.shared_context_changed is True


def test_short_summary_lists_specifics():
    a = _spec(specialists=[{"id": "x"}])
    b = _spec(specialists=[
        {"id": "x", "tools": ["atom.b.v1"]},
        {"id": "z"},
    ])
    diff = MultiAgentSpecDiffer().diff(a, b)
    summary = diff.short_summary()
    assert "+1 specialists" in summary or "z" in summary
    assert "field-changed" in summary or "tools" in summary or "specialists" in summary
