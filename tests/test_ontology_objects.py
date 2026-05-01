"""Smoke tests for Agent Ops Ontology Canonical Objects (blueprint §8)."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from app.ontology import (
    AgentCard,
    Artifact,
    CapabilityContract,
    EvalCase,
    EvalResult,
    PolicyDecision,
    RunEvent,
    StepRun,
    SuccessCriterion,
    TaskRun,
    WorkflowSpec,
    WorkflowStep,
)


# ---------- CapabilityContract ----------

def test_capability_contract_happy_path() -> None:
    cc = CapabilityContract(
        capability_id="ui.plan_blueprint",
        version="0.1.0",
        name="Plan UIBlueprint",
        description="Translate RequirementSpec into UIBlueprint.",
        owner="ui-team",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        risk_level="low",
    )
    dumped = cc.model_dump()
    assert dumped["capability_id"] == "ui.plan_blueprint"
    assert dumped["activation_status"] == "draft"
    # JSON Schema is exportable
    schema = CapabilityContract.model_json_schema()
    assert "properties" in schema and "capability_id" in schema["properties"]


def test_capability_contract_rejects_unknown_risk() -> None:
    with pytest.raises(ValueError):
        CapabilityContract(
            capability_id="x",
            version="0.1.0",
            name="x",
            description="x",
            owner="x",
            input_schema={},
            output_schema={},
            risk_level="catastrophic",  # not in Literal
        )


# ---------- AgentCard ----------

def test_agent_card_round_trip() -> None:
    card = AgentCard(
        agent_id="raytone.ui-agent",
        version="0.2.0",
        display_name="Raytone UI Agent",
        description="Generates shadcn/ui projects.",
        capabilities=["ui.plan_blueprint", "ui.generate_prototypes"],
        transport_type="local",
        supports_streaming=True,
    )
    j = card.model_dump_json()
    back = AgentCard.model_validate_json(j)
    assert back.capabilities == ["ui.plan_blueprint", "ui.generate_prototypes"]
    assert back.endpoint is None


# ---------- WorkflowSpec ----------

def test_workflow_spec_requires_at_least_one_step() -> None:
    with pytest.raises(ValueError):
        WorkflowSpec(workflow_id="empty", version="0.0.1", steps=[])


def test_workflow_spec_multi_step_dag() -> None:
    spec = WorkflowSpec(
        workflow_id="ui_delivery",
        version="0.1.0",
        name="UI Delivery",
        steps=[
            WorkflowStep(id="plan", capability="ui.plan_blueprint"),
            WorkflowStep(id="phase2", capability="ui.generate_prototypes", depends_on=["plan"]),
            WorkflowStep(id="phase3", capability="ui.generate_code", depends_on=["phase2"]),
            WorkflowStep(id="pkg", capability="delivery.package", depends_on=["phase3"]),
        ],
        success_criteria=[SuccessCriterion(kind="artifact_exists", value="code_artifact")],
    )
    assert len(spec.steps) == 4
    assert spec.steps[1].depends_on == ["plan"]


# ---------- TaskRun / StepRun ----------

def test_task_run_default_status_is_pending() -> None:
    tr = TaskRun(
        run_id="run-abc",
        workflow_id="ui_delivery",
        workflow_version="0.1.0",
        input_ref="artifact://xyz",
    )
    assert tr.status == "pending"
    assert tr.cost_summary.usd_estimate == 0.0


def test_step_run_lifecycle() -> None:
    sr = StepRun(
        step_run_id="sr-1",
        run_id="run-abc",
        step_id="plan",
        capability_id="ui.plan_blueprint",
        status="running",
        attempt=1,
        started_at=datetime.now(timezone.utc),
    )
    sr.status = "succeeded"
    sr.ended_at = datetime.now(timezone.utc)
    assert sr.status == "succeeded"


# ---------- Artifact ----------

def test_artifact_round_trip_and_type_constraint() -> None:
    a = Artifact(
        artifact_id="art-1",
        artifact_type="code_artifact",
        mime_type="application/zip",
        storage_uri="file:///tmp/app.zip",
        size_bytes=17092,
    )
    assert a.artifact_type == "code_artifact"
    with pytest.raises(ValueError):
        Artifact(
            artifact_id="art-2",
            artifact_type="unknown_kind",
            storage_uri="file:///tmp/x",
        )


# ---------- RunEvent ----------

def test_run_event_accepts_six_layer_tags() -> None:
    ev = RunEvent(
        event_id="evt-1",
        event_type="llm_request",
        timestamp=datetime.now(timezone.utc),
        layer="L1",
        component="LLMClient",
        kind="request",
        message="chat(model=claude-sonnet-4-6)",
    )
    assert ev.layer == "L1"
    # Payload can carry arbitrary JSON
    ev2 = RunEvent(
        event_id="evt-2",
        event_type="phase_started",
        timestamp=datetime.now(timezone.utc),
        layer="L5",
        component="Phase2",
        kind="start",
        payload={"blueprint_pages": 3},
    )
    assert ev2.payload["blueprint_pages"] == 3


# ---------- PolicyDecision ----------

def test_policy_decision_basic() -> None:
    pd = PolicyDecision(
        decision_id="pd-1",
        decision="require_approval",
        action_category="export",
        action_name="delivery.package",
        reason_code="RISK_MEDIUM_NEEDS_APPROVAL",
    )
    assert pd.decision == "require_approval"


# ---------- Eval ----------

def test_eval_case_and_result() -> None:
    case = EvalCase(
        eval_case_id="ec-1",
        suite_id="ui_golden",
        capability_id="ui.plan_blueprint",
        input_ref="fixtures/rfp/acme.json",
    )
    result = EvalResult(
        eval_result_id="er-1",
        eval_case_id=case.eval_case_id,
        outcome="pass",
        scores={"schema_validity": 1.0, "latency_ms": 4200.0},
    )
    assert result.scores["schema_validity"] == 1.0


# ---------- Top-level namespace re-export ----------

def test_top_level_ontology_namespace_exports() -> None:
    import app.ontology as ontology
    names = {"CapabilityContract", "AgentCard", "WorkflowSpec", "TaskRun",
             "StepRun", "Artifact", "RunEvent", "PolicyDecision",
             "EvalCase", "EvalResult", "UIBlueprint", "CodeArtifact"}
    missing = names - set(getattr(ontology, "__all__", []))
    assert not missing, f"Missing exports: {missing}"


# ---------- JSON Schema export (for future schemas/ folder) ----------

def test_all_objects_export_json_schema() -> None:
    for cls in [
        CapabilityContract, AgentCard, WorkflowSpec, WorkflowStep,
        TaskRun, StepRun, Artifact, RunEvent, PolicyDecision,
        EvalCase, EvalResult,
    ]:
        schema = cls.model_json_schema()
        # Round-trippable as JSON
        assert json.dumps(schema)
        assert "properties" in schema
