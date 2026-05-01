"""Tests for N8nCompilerImpl V1."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.pipelines.factory.compiler import N8nCompilerImpl
from app.core.pipelines.factory.ir import (
    ResolvedDAG,
    ResolvedEdge,
    ResolvedNode,
    TypeCheck,
)
from app.registry.atom_loader import AtomLoaderImpl


@pytest.fixture(scope="module")
def loaded_atoms():
    repo_root = Path(__file__).resolve().parents[7]
    base = repo_root / "capabilities" / "atom"
    if not base.exists():
        pytest.skip(f"no atom dir: {base}")
    return AtomLoaderImpl().load_all(base)


def _node(
    nid: str,
    asset_id: str,
    *,
    inputs: dict | None = None,
    params: dict | None = None,
    llm_task_type: str | None = None,
    llm_size: str | None = None,
):
    return ResolvedNode(
        id=nid,
        asset_id=asset_id,
        asset_version="1.0.0",
        confidence=0.9,
        selection_reason="test",
        inputs=inputs or {},
        params=params or {},
        llm_task_type=llm_task_type,
        llm_size=llm_size,
    )


def test_rejects_dify_target():
    c = N8nCompilerImpl()
    dag = ResolvedDAG(intent_ref="x", nodes=[], edges=[], target="dify")
    with pytest.raises(ValueError, match="n8n/hybrid"):
        c.compile(dag)


def test_compile_n8n_target_emits_json(loaded_atoms):
    c = N8nCompilerImpl()
    c.index(loaded_atoms)
    dag = ResolvedDAG(
        intent_ref="test",
        nodes=[
            _node("s1", "atom.schedule.cron.v1", params={"cron_expr": "0 9 * * 1"}),
            _node("s2", "atom.db.postgres.v1", inputs={"sql": "SELECT 1"}),
            _node("s3", "atom.notify.dingtalk.v1", inputs={"webhook_url": "https://x"}),
        ],
        edges=[
            ResolvedEdge(
                from_node="s1", to_node="s2", from_var="output", to_var="trigger",
                type_check=TypeCheck(from_type="void_signal", to_type="void_signal"),
            ),
            ResolvedEdge(
                from_node="s2", to_node="s3", from_var="output", to_var="content",
                type_check=TypeCheck(from_type="tabular_data", to_type="markdown_text"),
                needs_adapter=True,
            ),
        ],
        target="n8n",
    )
    out = c.compile(dag)
    parsed = json.loads(out)
    assert "nodes" in parsed
    assert "connections" in parsed
    assert len(parsed["nodes"]) == 3
    types = {n["type"] for n in parsed["nodes"]}
    assert "n8n-nodes-base.scheduleTrigger" in types
    assert "n8n-nodes-base.postgres" in types
    # Connections should chain s1 -> s2 -> s3
    assert len(parsed["connections"]) >= 2


def test_compile_hybrid_emits_only_n8n_nodes(loaded_atoms):
    c = N8nCompilerImpl()
    c.index(loaded_atoms)
    dag = ResolvedDAG(
        intent_ref="test",
        nodes=[
            _node("s1", "atom.schedule.cron.v1", params={"cron_expr": "0 9 * * 1"}),
            _node("s2", "atom.db.postgres.v1", inputs={"sql": "SELECT 1"}),
            _node(
                "s3", "atom.llm.chat.v1",
                llm_task_type="长文档分块总结", llm_size="中",
            ),
            _node("s4", "atom.notify.dingtalk.v1", inputs={"webhook_url": "x"}),
        ],
        edges=[],
        target="hybrid",
        target_split={"n8n_nodes": ["s1", "s2", "s4"], "dify_nodes": ["s3"]},
    )
    out = c.compile(dag)
    parsed = json.loads(out)
    node_names = [n["name"] for n in parsed["nodes"]]
    # s3 (dify) should NOT be in n8n output
    assert not any("(s3)" in name for name in node_names)
    assert any("(s1)" in name for name in node_names)
    assert any("(s2)" in name for name in node_names)
    assert any("(s4)" in name for name in node_names)


def test_compile_is_deterministic(loaded_atoms):
    c = N8nCompilerImpl()
    c.index(loaded_atoms)
    dag = ResolvedDAG(
        intent_ref="x",
        nodes=[_node("s1", "atom.db.postgres.v1", inputs={"sql": "SELECT 1"})],
        edges=[],
        target="n8n",
    )
    out1 = c.compile(dag)
    out2 = c.compile(dag)
    assert out1 == out2


def test_llm_node_carries_ruidong_placeholder(loaded_atoms):
    c = N8nCompilerImpl()
    c.index(loaded_atoms)
    dag = ResolvedDAG(
        intent_ref="x",
        nodes=[
            _node(
                "s1", "atom.llm.chat.v1",
                llm_task_type="结构化抽取", llm_size="中",
            )
        ],
        edges=[],
        target="n8n",
    )
    out = c.compile(dag)
    assert "RUIDONG_MODEL_FOR_" in out
    assert "结构化抽取" in out


def test_factory_metadata_preserved(loaded_atoms):
    c = N8nCompilerImpl()
    c.index(loaded_atoms)
    dag = ResolvedDAG(
        intent_ref="abc",
        pattern_id="combo.cron_etl_report.v1",
        nodes=[_node("s1", "atom.db.postgres.v1")],
        edges=[],
        target="n8n",
        issues=[{"kind": "low_confidence", "step_id": "s1"}],
    )
    parsed = json.loads(c.compile(dag))
    md = parsed["factory_metadata"]
    assert md["intent_ref"] == "abc"
    assert md["pattern_id"] == "combo.cron_etl_report.v1"
    assert md["target"] == "n8n"
    assert md["issues"][0]["kind"] == "low_confidence"
