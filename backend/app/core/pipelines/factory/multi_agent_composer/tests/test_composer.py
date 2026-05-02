"""Tests for OpenAIAgentsSDKComposerImpl - Phase 7 W1."""
from __future__ import annotations

import ast

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
from app.core.pipelines.factory.multi_agent_composer import (
    OpenAIAgentsSDKComposerImpl,
    _agent_var,
)


def _airline_cs_spec() -> MultiAgentSpec:
    return MultiAgentSpec(
        name="Airline Customer Service System",
        description="Triage + 5 specialists for airline customer service. Equivalent to openai/openai-cs-agents-demo.",
        industry=IndustryTag(
            code="01", primary="通用", sub=None, business_scenario="客服"
        ),
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
                handoff_targets=["booking", "faq"],
                tools=["atom.http.generic.v1"],
            ),
            SpecialistSpec(
                id="booking", name="Booking",
                agent_class="customer_service",
                description="Books, rebooks, or cancels trips on user request",
                nl_brief="处理订票、改签、退票、座位变更等请求",
                handoff_targets=["seat", "refunds"],
                tools=["atom.http.generic.v1"],
            ),
            SpecialistSpec(
                id="seat", name="Seat Services",
                agent_class="customer_service",
                description="Manages seats and medical/front-row requests",
                nl_brief="处理选座、特殊服务申请（前排、医疗）",
                handoff_targets=["flight_info"],
                tools=[],
            ),
            SpecialistSpec(
                id="faq", name="FAQ",
                agent_class="customer_service",
                description="Policy questions: baggage / compensation / wifi",
                nl_brief="回答行李、赔偿、wifi 等通用航司政策",
                handoff_targets=["refunds"],
                tools=[],
            ),
            SpecialistSpec(
                id="refunds", name="Refunds",
                agent_class="customer_service",
                description="Issues compensation cases after disruptions",
                nl_brief="处理延误赔偿、酒店餐补、退款 case",
                handoff_targets=["faq"],
                tools=[],
            ),
        ],
        handoffs=[
            HandoffEdge(from_specialist="flight_info", to_specialist="booking",
                        when="user wants to rebook after a delay"),
            HandoffEdge(from_specialist="faq", to_specialist="refunds",
                        when="user mentions delay > 3 hours and wants compensation"),
        ],
        guardrails=[
            GuardrailSpec(kind="relevance",
                          description="Block off-topic input that diverges from airline service"),
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


# ---- _agent_var helper ------------------------------------------------------


def test_agent_var_basic():
    assert _agent_var("flight_info") == "flight_info_agent"


def test_agent_var_strips_invalid_chars():
    assert _agent_var("ghost specialist!") == "ghost_specialist_agent"


def test_agent_var_handles_leading_digit():
    assert _agent_var("1st_agent") == "_1st_agent_agent"


# ---- compile happy path -----------------------------------------------------


def test_compile_produces_valid_python():
    spec = _airline_cs_spec()
    src = OpenAIAgentsSDKComposerImpl().compile(spec)
    # Must be parseable Python
    tree = ast.parse(src)
    assert tree is not None


def test_compile_emits_all_specialists_as_assigns():
    spec = _airline_cs_spec()
    src = OpenAIAgentsSDKComposerImpl().compile(spec)
    tree = ast.parse(src)
    assigned_names = {
        node.targets[0].id
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    }
    expected = {
        "flight_info_agent", "booking_agent", "seat_agent", "faq_agent",
        "refunds_agent", "triage_agent",
    }
    assert expected.issubset(assigned_names)


def test_compile_emits_required_exports():
    spec = _airline_cs_spec()
    src = OpenAIAgentsSDKComposerImpl().compile(spec)
    assert "ENTRY_AGENT = triage_agent" in src
    assert "ALL_AGENTS = [triage_agent" in src
    assert "SPECIALIST_AGENTS = [" in src
    assert "HANDOFF_RULES" in src
    assert "GUARDRAILS" in src
    assert "SHARED_CONTEXT_FIELDS" in src


def test_compile_two_pass_handoff_wiring():
    """Specialists declared first (empty handoffs); wired in second pass."""
    spec = _airline_cs_spec()
    src = OpenAIAgentsSDKComposerImpl().compile(spec)
    # First-pass declaration always uses handoffs=[]
    assert "handoffs=[],  # wired in second pass below" in src
    # Second-pass wiring uses .handoffs assignment
    assert "flight_info_agent.handoffs = [booking_agent, faq_agent]" in src
    assert "faq_agent.handoffs = [refunds_agent]" in src


def test_compile_triage_handoffs_inline():
    """Triage agent declares handoffs inline (since all specialists already declared above)."""
    spec = _airline_cs_spec()
    src = OpenAIAgentsSDKComposerImpl().compile(spec)
    # All 5 specialist vars in triage handoffs list
    for var in [
        "flight_info_agent", "booking_agent", "seat_agent", "faq_agent", "refunds_agent",
    ]:
        # Inline assigment within triage_agent block (loose check)
        assert var in src


def test_compile_emits_handoff_rules_dict():
    spec = _airline_cs_spec()
    src = OpenAIAgentsSDKComposerImpl().compile(spec)
    assert '"from": "flight_info"' in src
    assert '"to": "booking"' in src
    assert '"when": "user wants to rebook after a delay"' in src


def test_compile_emits_guardrail_metadata():
    spec = _airline_cs_spec()
    src = OpenAIAgentsSDKComposerImpl().compile(spec)
    assert '"kind": "relevance"' in src
    assert '"kind": "jailbreak"' in src
    assert '"blocking": True' in src


def test_compile_emits_shared_context_fields():
    spec = _airline_cs_spec()
    src = OpenAIAgentsSDKComposerImpl().compile(spec)
    assert '"name": "confirmation_number"' in src
    assert '"name": "flight_number"' in src


def test_compile_idempotent():
    """Same spec compiles to byte-identical source."""
    spec = _airline_cs_spec()
    a = OpenAIAgentsSDKComposerImpl().compile(spec)
    b = OpenAIAgentsSDKComposerImpl().compile(spec)
    assert a == b


def test_compile_rejects_unsupported_runtime():
    spec = _airline_cs_spec()
    spec.runtime = "openai_agents_sdk"  # ensure baseline
    OpenAIAgentsSDKComposerImpl().compile(spec)  # ok
    # Now corrupt and expect rejection
    object.__setattr__(spec, "runtime", "some_other_runtime")
    with pytest.raises(ValueError, match="openai_agents_sdk"):
        OpenAIAgentsSDKComposerImpl().compile(spec)


def test_compile_rejects_spec_with_structural_issues():
    spec = _airline_cs_spec()
    # Inject ghost handoff to break validate_graph
    spec.handoffs.append(HandoffEdge(
        from_specialist="flight_info", to_specialist="ghost",
        when="never fires",
    ))
    with pytest.raises(ValueError, match="structural issues"):
        OpenAIAgentsSDKComposerImpl().compile(spec)


def test_compile_handles_no_handoffs_no_guardrails():
    """Minimal spec: no handoffs, no guardrails, no shared context."""
    spec = MultiAgentSpec(
        name="Bare Minimum",
        description="Smallest valid multi-agent spec for compiler test",
        industry=IndustryTag(code="01", primary="通用"),
        triage=TriageSpec(system_prompt_id="prompt.triage.minimal.v1"),
        specialists=[
            SpecialistSpec(
                id="solo", name="Solo Specialist",
                agent_class="customer_service",
                description="A single specialist for minimal smoke test",
                nl_brief="处理任意用户请求并直接回答",
            ),
        ],
    )
    src = OpenAIAgentsSDKComposerImpl().compile(spec)
    ast.parse(src)
    assert "HANDOFF_RULES: list[dict] = []" in src
    assert "GUARDRAILS: list[dict] = []" in src
    assert "SHARED_CONTEXT_FIELDS: list[dict] = []" in src


def test_compile_escapes_quotes_in_descriptions():
    """Strings containing double-quotes must be escaped properly."""
    spec = MultiAgentSpec(
        name='System with "quotes" in name',
        description='Description with "embedded" quotes for testing',
        industry=IndustryTag(code="01", primary="通用"),
        triage=TriageSpec(system_prompt_id="prompt.triage.test.v1"),
        specialists=[
            SpecialistSpec(
                id="quoted", name='Agent with "quotes"',
                agent_class="customer_service",
                description='Role with "embedded" quotes for escaping test',
                nl_brief='Brief with "more" quotes for escaping test',
            ),
        ],
    )
    src = OpenAIAgentsSDKComposerImpl().compile(spec)
    ast.parse(src)  # Critical: must remain syntactically valid
