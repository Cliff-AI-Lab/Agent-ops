"""Tests for DifyCompilerImpl V2 (Phase 8 Day 1).

V2 changes vs V1:
- Top-level adds version='0.4.0' / kind='app' / dependencies=[].
- Workflow adds conversation_variables / environment_variables / features /
  graph.viewport.
- Every node has top-level type='custom'; the real Dify type lives at
  ``data.type`` (start | llm | http-request | code | end). Old V1 tests
  asserted the legacy WRONG contract (n["type"] == "llm") which would have
  always been rejected by a real Dify import.
- Synthetic Start + End nodes are auto-prepended/appended.
- Factory-private metadata moves to top-level ``_factory_metadata``
  (underscore prefix; Dify Pydantic ignores extra keys).
"""

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


def test_top_level_dify_dsl_keys(loaded_atoms):
    """v2: emit Dify-compatible top-level keys (version + kind + dependencies)."""
    c = DifyCompilerImpl()
    c.index(loaded_atoms)
    dag = ResolvedDAG(
        intent_ref="t",
        nodes=[_node("s1", "atom.llm.chat.v1", llm_task_type="对话", llm_size="中")],
        edges=[],
        target="dify",
    )
    parsed = yaml.safe_load(c.compile(dag))
    assert parsed["version"] == "0.4.0"
    assert parsed["kind"] == "app"
    assert parsed["dependencies"] == []
    assert parsed["app"]["mode"] == "workflow"
    assert "icon" in parsed["app"]
    assert parsed["app"]["use_icon_as_answer_icon"] is False
    assert parsed["workflow"]["conversation_variables"] == []
    assert parsed["workflow"]["environment_variables"] == []
    assert parsed["workflow"]["features"] == {}
    assert parsed["workflow"]["graph"]["viewport"] == {"x": 0, "y": 0, "zoom": 0.7}


def test_node_top_level_type_is_custom_real_type_in_data(loaded_atoms):
    """v2: top-level type='custom' for ALL nodes; real type in data.type."""
    c = DifyCompilerImpl()
    c.index(loaded_atoms)
    dag = ResolvedDAG(
        intent_ref="t",
        nodes=[_node("s1", "atom.llm.chat.v1", llm_task_type="对话", llm_size="中")],
        edges=[],
        target="dify",
    )
    parsed = yaml.safe_load(c.compile(dag))
    nodes = parsed["workflow"]["graph"]["nodes"]
    assert all(n["type"] == "custom" for n in nodes), (
        "every Dify node must have top-level type='custom'"
    )
    data_types = [n["data"]["type"] for n in nodes]
    assert data_types[0] == "start"
    assert data_types[-1] == "end"
    middle = [t for t in data_types if t not in {"start", "end"}]
    assert middle == ["llm"]


def test_synthetic_start_and_end_nodes(loaded_atoms):
    """Dify requires every workflow to begin with start, end with end."""
    c = DifyCompilerImpl()
    c.index(loaded_atoms)
    dag = ResolvedDAG(
        intent_ref="t",
        nodes=[_node("s1", "atom.db.postgres.v1")],
        edges=[],
        target="dify",
    )
    parsed = yaml.safe_load(c.compile(dag))
    nodes = parsed["workflow"]["graph"]["nodes"]
    edges = parsed["workflow"]["graph"]["edges"]
    ids = [n["id"] for n in nodes]
    assert "node_start" == ids[0]
    assert "node_end" == ids[-1]
    assert "s1" in ids
    edge_pairs = [(e["source"], e["target"]) for e in edges]
    assert ("node_start", "s1") in edge_pairs
    assert ("s1", "node_end") in edge_pairs


def test_edge_v2_schema(loaded_atoms):
    """Edges must carry id / sourceHandle / targetHandle / type=custom / data."""
    c = DifyCompilerImpl()
    c.index(loaded_atoms)
    dag = ResolvedDAG(
        intent_ref="t",
        nodes=[
            _node("s1", "atom.db.postgres.v1"),
            _node("s2", "atom.llm.chat.v1", llm_task_type="对话", llm_size="中"),
        ],
        edges=[
            ResolvedEdge(
                from_node="s1",
                to_node="s2",
                from_var="output",
                to_var="data",
                type_check=TypeCheck(from_type="tabular_data", to_type="unknown"),
            )
        ],
        target="dify",
    )
    parsed = yaml.safe_load(c.compile(dag))
    edges = parsed["workflow"]["graph"]["edges"]
    s1_to_s2 = next((e for e in edges if e["source"] == "s1" and e["target"] == "s2"), None)
    assert s1_to_s2 is not None
    assert s1_to_s2["sourceHandle"] == "source"
    assert s1_to_s2["targetHandle"] == "target"
    assert s1_to_s2["type"] == "custom"
    assert s1_to_s2["data"]["sourceType"] == "code"
    assert s1_to_s2["data"]["targetType"] == "llm"
    assert s1_to_s2["data"]["isInIteration"] is False


def test_compile_llm_node_uses_ruidong_gateway(loaded_atoms):
    """LLM node model.name must contain RUIDONG_MODEL_FOR placeholder.

    The placeholder is resolved at runtime via iruidong.com/v1 gateway
    (no hardcoded model name per project rule)."""
    c = DifyCompilerImpl()
    c.index(loaded_atoms)
    dag = ResolvedDAG(
        intent_ref="x",
        nodes=[_node("s1", "atom.llm.chat.v1", llm_task_type="通用对话", llm_size="中", fallback=["中", "大"])],
        edges=[],
        target="dify",
    )
    parsed = yaml.safe_load(c.compile(dag))
    llm_node = next(
        n for n in parsed["workflow"]["graph"]["nodes"] if n["data"]["type"] == "llm"
    )
    assert "RUIDONG_MODEL_FOR" in llm_node["data"]["model"]["name"]
    assert llm_node["data"]["_llm_routing"]["task_type"] == "通用对话"
    assert llm_node["data"]["_llm_routing"]["fallback_chain"] == ["中", "大"]


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
    user_node_ids = {n["id"] for n in nodes if n["id"] not in {"node_start", "node_end"}}
    assert user_node_ids == {"s3"}
    edge_pairs = [(e["source"], e["target"]) for e in edges]
    assert ("node_start", "s3") in edge_pairs
    assert ("s3", "node_end") in edge_pairs


def test_compile_is_deterministic(loaded_atoms):
    """Same DAG twice -> same output (R3 determinism).

    NOTE: LLM node prompt_template uses uuid for system message id.
    For determinism we strip those uuids before comparison.
    """
    import re

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
    uuid_re = re.compile(r"id: [0-9a-f-]{36}")
    assert uuid_re.sub("id: <uuid>", out1) == uuid_re.sub("id: <uuid>", out2)


def test_factory_metadata_at_top_level(loaded_atoms):
    """v2: factory_metadata moved from workflow.* to top-level _factory_metadata."""
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
    parsed = yaml.safe_load(c.compile(dag))
    assert "factory_metadata" not in parsed["workflow"], (
        "workflow.factory_metadata removed in v2; Dify schema does not allow it"
    )
    md = parsed["_factory_metadata"]
    assert md["intent_ref"] == "abc"
    assert md["pattern_id"] == "combo.cron_etl_report.v1"
    assert md["target"] == "dify"
    assert md["issues"] == [{"kind": "no_candidate", "step_id": "s9"}]


def test_node_position_layout(loaded_atoms):
    """Nodes are laid out left-to-right with x = 30 + 304*i, y = 245."""
    c = DifyCompilerImpl()
    c.index(loaded_atoms)
    dag = ResolvedDAG(
        intent_ref="t",
        nodes=[_node("s1", "atom.db.postgres.v1"), _node("s2", "atom.notify.dingtalk.v1")],
        edges=[],
        target="dify",
    )
    parsed = yaml.safe_load(c.compile(dag))
    nodes = parsed["workflow"]["graph"]["nodes"]
    xs = [n["position"]["x"] for n in nodes]
    assert xs == [30, 334, 638, 942]  # start, s1, s2, end
    assert all(n["position"]["y"] == 245 for n in nodes)
    assert all(n["width"] == 244 and n["height"] == 90 for n in nodes)


def test_structural_alignment_with_dify_golden_workflow_llm():
    """Compile a minimal Start->LLM->End equivalent and check it has the same
    top-level + node + edge shape as Dify's official sample
    `scripts/stress-test/setup/dsl/workflow_llm.yml` (DSL v0.4.0).

    We do NOT require byte equality (Dify ids are timestamps, ours are 'sN').
    We do require the schema shape matches: keys present, types correct.
    """
    repo_root = Path(__file__).resolve().parents[7]
    atom_base = repo_root / "capabilities" / "atom"
    if not atom_base.exists():
        pytest.skip(f"no atom dir: {atom_base}")
    atoms = AtomLoaderImpl().load_all(atom_base)

    c = DifyCompilerImpl()
    c.index(atoms)
    dag = ResolvedDAG(
        intent_ref="golden",
        nodes=[_node("s1", "atom.llm.chat.v1", llm_task_type="通用对话", llm_size="中")],
        edges=[],
        target="dify",
    )
    parsed = yaml.safe_load(c.compile(dag))

    # Top-level keys present in golden
    for k in ("version", "kind", "app", "dependencies", "workflow"):
        assert k in parsed, f"missing top-level key: {k}"

    # App-level keys present in golden
    for k in ("description", "icon", "icon_background", "mode", "name", "use_icon_as_answer_icon"):
        assert k in parsed["app"], f"missing app key: {k}"

    # Workflow-level keys present in golden
    for k in ("conversation_variables", "environment_variables", "features", "graph"):
        assert k in parsed["workflow"], f"missing workflow key: {k}"

    # Graph-level keys present in golden
    for k in ("edges", "nodes", "viewport"):
        assert k in parsed["workflow"]["graph"], f"missing graph key: {k}"

    # Each node has the keys golden uses
    for n in parsed["workflow"]["graph"]["nodes"]:
        for k in ("data", "id", "position", "positionAbsolute", "selected",
                  "sourcePosition", "targetPosition", "type", "width", "height"):
            assert k in n, f"node missing key: {k}; got {list(n.keys())}"
        assert n["type"] == "custom"
        assert n["sourcePosition"] == "right"
        assert n["targetPosition"] == "left"

    # Each edge has the keys golden uses
    for e in parsed["workflow"]["graph"]["edges"]:
        for k in ("data", "id", "source", "sourceHandle", "target", "targetHandle", "type"):
            assert k in e, f"edge missing key: {k}; got {list(e.keys())}"
        assert e["type"] == "custom"
        assert e["sourceHandle"] == "source"
        assert e["targetHandle"] == "target"
