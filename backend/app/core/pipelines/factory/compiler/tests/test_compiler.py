"""Tests for DifyCompilerImpl V1."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.core.pipelines.factory.compiler import DSLCompiler, DifyCompilerImpl
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
    llm_task_type=None,
    llm_size=None,
    fallback=None,
):
    return ResolvedNode(
        id=nid,
        asset_id=asset_id,
        asset_version="1.0.0",
        confidence=0.9,
        selection_reason="test",
        llm_task_type=llm_task_type,
        llm_size=llm_size,
        llm_fallback_chain=list(fallback or []),
    )


def test_interface_importable():
    assert DSLCompiler is not None


def test_rejects_n8n_target():
    c = DifyCompilerImpl()
    dag = ResolvedDAG(intent_ref="x", nodes=[], edges=[], target="n8n")
    with pytest.raises(ValueError, match="dify/hybrid"):
        c.compile(dag)


def test_compile_dify_target_emits_yaml(loaded_atoms):
    c = DifyCompilerImpl()
    c.index(loaded_atoms)
    dag = ResolvedDAG(
        intent_ref="test",
        nodes=[_node("s1", "atom.llm.chat.v1", llm_task_type="通用对话", llm_size="中", fallback=["中", "大"])],
        edges=[],
        target="dify",
    )
    out = c.compile(dag)
    parsed = yaml.safe_load(out)
    assert "app" in parsed
    assert "workflow" in parsed
    assert len(parsed["workflow"]["graph"]["nodes"]) == 1
    n = parsed["workflow"]["graph"]["nodes"][0]
    assert n["id"] == "s1"
    assert n["type"] == "llm"
    assert n["data"]["llm_routing"]["task_type"] == "通用对话"
    assert "RUIDONG_MODEL_FOR" in n["data"]["llm_routing"]["model_placeholder"]


def test_compile_hybrid_emits_only_dify_nodes(loaded_atoms):
    c = DifyCompilerImpl()
    c.index(loaded_atoms)
    dag = ResolvedDAG(
        intent_ref="test",
        nodes=[
            _node("s1", "atom.schedule.cron.v1"),
            _node("s2", "atom.db.postgres.v1"),
            _node("s3", "atom.llm.chat.v1", llm_task_type="通用对话", llm_size="中", fallback=["中"]),
            _node("s4", "atom.notify.dingtalk.v1"),
        ],
        edges=[
            ResolvedEdge(
                from_node="s2", to_node="s3", from_var="output", to_var="data",
                type_check=TypeCheck(from_type="tabular_data", to_type="unknown"),
                needs_adapter=False,
            ),
            ResolvedEdge(
                from_node="s3", to_node="s4", from_var="output", to_var="content",
                type_check=TypeCheck(from_type="natural_language", to_type="markdown_text"),
                needs_adapter=True,
            ),
        ],
        target="hybrid",
        target_split={"n8n_nodes": ["s1", "s2", "s4"], "dify_nodes": ["s3"]},
    )
    out = c.compile(dag)
    parsed = yaml.safe_load(out)
    nodes = parsed["workflow"]["graph"]["nodes"]
    edges = parsed["workflow"]["graph"]["edges"]
    assert {n["id"] for n in nodes} == {"s3"}
    # edges scoped to dify_nodes only -> none of the original 2 edges qualify
    assert edges == []


def test_compile_is_deterministic(loaded_atoms):
    """Same DAG twice -> same output (R3 determinism)."""
    c = DifyCompilerImpl()
    c.index(loaded_atoms)
    dag = ResolvedDAG(
        intent_ref="x",
        nodes=[_node("s1", "atom.db.postgres.v1")],
        edges=[],
        target="dify",
    )
    out1 = c.compile(dag)
    out2 = c.compile(dag)
    assert out1 == out2


def test_compile_emits_factory_metadata(loaded_atoms):
    c = DifyCompilerImpl()
    c.index(loaded_atoms)
    dag = ResolvedDAG(
        intent_ref="abc",
        pattern_id="combo.cron_etl_report.v1",
        nodes=[_node("s1", "atom.db.postgres.v1")],
        edges=[],
        target="dify",
        issues=[{"kind": "no_candidate", "step_id": "s9"}],
    )
    out = c.compile(dag)
    parsed = yaml.safe_load(out)
    md = parsed["workflow"]["factory_metadata"]
    assert md["intent_ref"] == "abc"
    assert md["pattern_id"] == "combo.cron_etl_report.v1"
    assert md["target"] == "dify"
    assert md["issues"] == [{"kind": "no_candidate", "step_id": "s9"}]
