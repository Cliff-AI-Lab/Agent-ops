"""Tests for Phase 7 MultiAgentSpec IR."""
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


def _airline_cs_fixture() -> MultiAgentSpec:
    """cs-agents-demo equivalent: airline customer service."""
    return MultiAgentSpec(
        name="Airline Customer Service System",
        description="Triage + 5 specialists for airline customer service. Equivalent to openai/openai-cs-agents-demo.",
        industry=IndustryTag(code="01", primary="通用", sub="客服", business_scenario="航司客服"),
        triage=TriageSpec(
            system_prompt_id="prompt.triage.airline_cs.v1",
            initial_handoff_targets=["flight_info", "booking", "seat", "faq", "refunds"],
        ),
        specialists=[
            SpecialistSpec(
                id="flight_info", name="Flight Information",
                agent_class="customer_service",
                description="Live status / connection risk / alternates",
                nl_brief="查询航班实时状态、中转风险、替代航班建议",
                handoff_targets=["booking", "seat", "faq"],
                tools=["atom.http.generic.v1"],
            ),
            SpecialistSpec(
                id="booking", name="Booking & Cancellation",
                agent_class="customer_service",
                description="Books, rebooks, or cancels trips",
                nl_brief="处理订票、改签、退票",
                handoff_targets=["seat", "refunds"],
                tools=["atom.http.generic.v1", "atom.notify.dingtalk.v1"],
            ),
            SpecialistSpec(
                id="seat", name="Seat & Special Services",
                agent_class="customer_service",
                description="Manages seats and medical/front-row requests",
                nl_brief="选座、特殊服务申请（前排、医疗）",
                handoff_targets=["flight_info"],
                tools=["atom.http.generic.v1"],
            ),
            SpecialistSpec(
                id="faq", name="FAQ",
                agent_class="customer_service",
                description="Policy questions: baggage / compensation / wifi",
                nl_brief="回答行李、赔偿、wifi 等政策问题",
                handoff_targets=["refunds"],
                tools=[],
            ),
            SpecialistSpec(
                id="refunds", name="Refunds & Compensation",
                agent_class="customer_service",
                description="Issues compensation cases after disruptions",
                nl_brief="处理延误赔偿、酒店餐补、退款",
                handoff_targets=["faq"],
                tools=["atom.http.generic.v1", "atom.notify.dingtalk.v1"],
            ),
        ],
        handoffs=[
            HandoffEdge(from_specialist="flight_info", to_specialist="booking",
                        when="user wants to rebook after a delay"),
            HandoffEdge(from_specialist="booking", to_specialist="seat",
                        when="user asks about seat after booking change"),
            HandoffEdge(from_specialist="seat", to_specialist="flight_info",
                        when="user asks about flight status mid-seat-change"),
            HandoffEdge(from_specialist="faq", to_specialist="refunds",
                        when="user mentions delay > 3 hours and wants compensation"),
        ],
        guardrails=[
            GuardrailSpec(kind="relevance",
                          description="Block off-topic (only airline travel allowed)"),
            GuardrailSpec(kind="jailbreak",
                          description="Block prompt injection / system instruction extraction"),
        ],
        shared_context=[
            SharedContextField(name="confirmation_number", type="string",
                                description="passenger confirmation code"),
            SharedContextField(name="flight_number", type="string",
                                description="current flight identifier"),
        ],
    )


def test_airline_cs_fixture_constructs():
    spec = _airline_cs_fixture()
    assert spec.name == "Airline Customer Service System"
    assert len(spec.specialists) == 5
    assert spec.runtime == "openai_agents_sdk"


def test_airline_cs_validate_graph_clean():
    spec = _airline_cs_fixture()
    issues = spec.validate_graph()
    assert issues == [], f"unexpected issues: {issues}"


def test_validate_graph_detects_unknown_handoff_target():
    spec = _airline_cs_fixture()
    spec.handoffs.append(HandoffEdge(
        from_specialist="flight_info", to_specialist="ghost_specialist",
        when="never fires",
    ))
    issues = spec.validate_graph()
    assert any("ghost_specialist" in i for i in issues)


def test_validate_graph_detects_unknown_triage_target():
    spec = _airline_cs_fixture()
    spec.triage.initial_handoff_targets.append("ghost")
    issues = spec.validate_graph()
    assert any("ghost" in i for i in issues)


def test_specialist_ids_must_be_unique():
    with pytest.raises(ValueError, match="unique"):
        MultiAgentSpec(
            name="Dup",
            description="Two specialists with same id should fail validation",
            industry=IndustryTag(code="01", primary="通用"),
            triage=TriageSpec(system_prompt_id="prompt.x.v1"),
            specialists=[
                SpecialistSpec(id="a", name="A", agent_class="customer_service",
                                description="role description for A long enough",
                                nl_brief="brief description A long enough"),
                SpecialistSpec(id="a", name="A2", agent_class="customer_service",
                                description="role description for A2 long enough",
                                nl_brief="brief description A2 long enough"),
            ],
        )


def test_industry_code_format():
    with pytest.raises(ValueError):
        IndustryTag(code="001", primary="通用")  # 3 chars, must be 2


def test_specialist_id_alnum_only():
    with pytest.raises(ValueError, match="alnum"):
        SpecialistSpec(id="bad id with spaces", name="X",
                       agent_class="customer_service",
                       description="role description here",
                       nl_brief="brief description here")


def test_round_trip_serialization():
    spec = _airline_cs_fixture()
    json_str = spec.model_dump_json()
    restored = MultiAgentSpec.model_validate_json(json_str)
    assert restored.name == spec.name
    assert len(restored.specialists) == len(spec.specialists)
    assert restored.validate_graph() == []


def test_min_specialists_one():
    """At least 1 specialist required (a system without specialists is just a single agent)."""
    with pytest.raises(ValueError):
        MultiAgentSpec(
            name="Empty",
            description="No specialists; should fail min_length",
            industry=IndustryTag(code="01", primary="通用"),
            triage=TriageSpec(system_prompt_id="prompt.x.v1"),
            specialists=[],
        )
