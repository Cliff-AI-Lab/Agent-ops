"""Real Pydantic round-trip tests for factory IR.

These pass on Day 1 (no implementation needed, just IR contracts).
"""

from app.core.pipelines.factory.ir import (
    Constraints,
    OutputSpec,
    ResolvedDAG,
    ResolvedEdge,
    ResolvedNode,
    StepSpec,
    StructuredIntent,
    TriggerSpec,
    TypeCheck,
)


def test_trigger_spec_cron():
    t = TriggerSpec(type="cron", cron_expr="0 9 * * 1", natural_language="每周一上午9点")
    assert t.type == "cron"
    assert t.cron_expr == "0 9 * * 1"


def test_step_spec_minimal():
    s = StepSpec(id="s1", verb="查询数据库", expected_output_kind="tabular_data")
    assert s.id == "s1"
    assert s.inputs == {}
    assert s.constraints == {}


def test_structured_intent_roundtrip():
    intent = StructuredIntent(
        goal="销售周报",
        trigger=TriggerSpec(type="cron", cron_expr="0 9 * * 1"),
        steps=[
            StepSpec(id="s1", verb="查询数据库", expected_output_kind="tabular_data"),
            StepSpec(
                id="s2",
                verb="撰写周报",
                expected_output_kind="natural_language",
                inputs={"data": "$s1.output"},
                suggested_subcategory="LLM",
            ),
        ],
        outputs=[OutputSpec(name="status", type="void", sink="dingtalk:运营群")],
        raw_user_input="每周一上午 9 点...",
    )
    payload = intent.model_dump()
    rebuilt = StructuredIntent.model_validate(payload)
    assert rebuilt == intent
    assert rebuilt.schema_version == "1.0"
    assert len(rebuilt.steps) == 2


def test_constraints_default_zh_internal():
    c = Constraints()
    assert c.language == "zh"
    assert c.privacy == "internal"


def test_resolved_node_requires_selection_reason():
    """selection_reason is mandatory (non-empty) for trace."""
    import pytest

    with pytest.raises(Exception):
        ResolvedNode(
            id="s1",
            asset_id="atom.db.postgres.v1",
            asset_version="1.0.0",
            confidence=0.9,
            selection_reason="",  # empty -> should fail
        )


def test_resolved_node_with_llm_routing():
    """LLM nodes carry task_type+size, NOT specific model name."""
    n = ResolvedNode(
        id="s2",
        asset_id="atom.llm.chat.v1",
        asset_version="1.0.0",
        confidence=0.95,
        selection_reason="结构化抽取适合 task=结构化抽取/size=中",
        llm_task_type="结构化抽取",
        llm_size="中",
        llm_fallback_chain=["中", "大"],
        prompt_id="prompt.data.anomaly_detect.v1",
    )
    assert n.llm_task_type == "结构化抽取"
    assert n.llm_size == "中"
    assert "中" in n.llm_fallback_chain


def test_resolved_edge_with_type_check():
    """v2 audit gap #1: edge MUST carry type_check."""
    e = ResolvedEdge(
        from_node="s1",
        to_node="s2",
        from_var="output",
        to_var="data",
        type_check=TypeCheck(from_type="tabular_data", to_type="tabular_data"),
    )
    assert e.type_check.from_type == "tabular_data"
    assert e.needs_adapter is False


def test_resolved_dag_minimal():
    dag = ResolvedDAG(
        intent_ref="intent_001",
        nodes=[],
        edges=[],
        target="dify",
    )
    assert dag.target == "dify"
    assert dag.schema_version == "1.0"


def test_resolved_dag_hybrid_with_split():
    dag = ResolvedDAG(
        intent_ref="intent_001",
        nodes=[],
        edges=[],
        target="hybrid",
        target_split={"n8n_nodes": ["s1", "s4"], "dify_nodes": ["s2", "s3"]},
    )
    assert dag.target == "hybrid"
    assert "s1" in dag.target_split["n8n_nodes"]
