"""Phase 10 W1 Day 3/4 - tests for ReverseCompiler."""

from __future__ import annotations

import yaml

import pytest

from app.core.pipelines.factory.compiler import DifyCompilerImpl
from app.core.pipelines.factory.ir import (
    ResolvedDAG,
    ResolvedEdge,
    ResolvedNode,
    TypeCheck,
)
from app.delivery.reverse_compiler import ReverseCompiler, ReverseDiff


def _node(nid: str, asset_id: str) -> ResolvedNode:
    return ResolvedNode(
        id=nid, asset_id=asset_id, asset_version="1.0.0",
        confidence=0.9, selection_reason="test",
    )


def _dag(node_specs: list[tuple[str, str]]) -> ResolvedDAG:
    return ResolvedDAG(
        intent_ref="test",
        nodes=[_node(i, a) for i, a in node_specs],
        edges=[],
        target="dify",
    )


def _compile_yaml(dag: ResolvedDAG) -> str:
    """Use the real DifyCompiler so YAML carries _factory.asset_id like prod."""
    return DifyCompilerImpl().compile(dag)


def test_no_change_human_tuned_false():
    dag = _dag([("s1", "atom.db.postgres.v1"), ("s2", "atom.llm.chat.v1")])
    yaml_text = _compile_yaml(dag)
    diff = ReverseCompiler().diff("sp", dag, yaml_text)
    assert diff.human_tuned is False
    assert diff.added == []
    assert diff.removed == []
    assert diff.reordered is False


def test_user_added_atom_detected():
    dag = _dag([("s1", "atom.db.postgres.v1")])
    edited_dag = _dag([("s1", "atom.db.postgres.v1"), ("s2", "atom.llm.chat.v1")])
    edited_yaml = _compile_yaml(edited_dag)
    diff = ReverseCompiler().diff("sp", dag, edited_yaml)
    assert diff.human_tuned is True
    assert diff.added == ["atom.llm.chat.v1"]
    assert diff.removed == []


def test_user_removed_atom_detected():
    dag = _dag([("s1", "atom.db.postgres.v1"), ("s2", "atom.llm.chat.v1")])
    edited_dag = _dag([("s1", "atom.db.postgres.v1")])
    edited_yaml = _compile_yaml(edited_dag)
    diff = ReverseCompiler().diff("sp", dag, edited_yaml)
    assert diff.human_tuned is True
    assert diff.removed == ["atom.llm.chat.v1"]
    assert diff.added == []


def test_user_reordered_atoms_detected():
    dag = _dag([("s1", "atom.db.postgres.v1"), ("s2", "atom.llm.chat.v1")])
    swapped = _dag([("s2", "atom.llm.chat.v1"), ("s1", "atom.db.postgres.v1")])
    edited_yaml = _compile_yaml(swapped)
    diff = ReverseCompiler().diff("sp", dag, edited_yaml)
    assert diff.reordered is True
    assert diff.added == [] and diff.removed == []
    assert diff.human_tuned is True


def test_replacement_appears_as_add_plus_remove():
    dag = _dag([("s1", "atom.http.generic.v1")])
    replaced = _dag([("s1", "atom.notify.dingtalk.v1")])
    edited_yaml = _compile_yaml(replaced)
    diff = ReverseCompiler().diff("sp", dag, edited_yaml)
    assert "atom.notify.dingtalk.v1" in diff.added
    assert "atom.http.generic.v1" in diff.removed
    assert diff.human_tuned is True


def test_synthetic_start_end_nodes_ignored():
    dag = _dag([("s1", "atom.db.postgres.v1")])
    yaml_text = _compile_yaml(dag)
    parsed = yaml.safe_load(yaml_text)
    node_ids = [n["id"] for n in parsed["workflow"]["graph"]["nodes"]]
    # confirm the test fixture really contains synthetic nodes
    assert "node_start" in node_ids and "node_end" in node_ids
    diff = ReverseCompiler().diff("sp", dag, yaml_text)
    assert diff.original_atoms == ["atom.db.postgres.v1"]
    assert diff.edited_atoms == ["atom.db.postgres.v1"]


def test_invalid_yaml_returns_parse_error():
    dag = _dag([("s1", "atom.db.postgres.v1")])
    diff = ReverseCompiler().diff("sp", dag, "this is :: not valid yaml ::: at all\n--- broken")
    # most "broken" yaml strings still parse to a string scalar, so we also
    # check the explicit non-mapping path
    if diff.parse_error is None:
        # then it parsed but was not a mapping
        diff2 = ReverseCompiler().diff("sp", dag, "just a scalar string")
        assert diff2.parse_error is not None
        assert diff2.human_tuned is False


def test_yaml_missing_graph_returns_parse_error():
    dag = _dag([("s1", "atom.db.postgres.v1")])
    bad = "version: '0.4.0'\nkind: app\napp: {name: x}\n"  # no workflow.graph
    diff = ReverseCompiler().diff("sp", dag, bad)
    assert diff.parse_error is not None
    assert "workflow.graph.nodes" in diff.parse_error or "missing" in diff.parse_error
    assert diff.human_tuned is False


def test_node_without_factory_metadata_skipped():
    """User-added node without _factory.asset_id is skipped (W2 will infer)."""
    dag = _dag([("s1", "atom.db.postgres.v1")])
    yaml_text = _compile_yaml(dag)
    parsed = yaml.safe_load(yaml_text)
    # synthetic add: a node without _factory
    parsed["workflow"]["graph"]["nodes"].append({
        "id": "human_added",
        "type": "custom",
        "position": {"x": 1500, "y": 245},
        "data": {"type": "code", "title": "manual"},
    })
    edited = yaml.safe_dump(parsed, allow_unicode=True, sort_keys=False)
    diff = ReverseCompiler().diff("sp", dag, edited)
    # ours should still equal original since human_added has no _factory
    assert diff.added == []
    assert diff.removed == []
    assert diff.human_tuned is False


def test_short_summary_messages():
    base = ReverseDiff(
        specimen_id="x", original_atoms=["a"], edited_atoms=["a"],
        human_tuned=False,
    )
    assert "no change" in base.short_summary()

    err = ReverseDiff(
        specimen_id="x", original_atoms=[], edited_atoms=[],
        parse_error="bad yaml", human_tuned=False,
    )
    assert "parse error" in err.short_summary()

    add = ReverseDiff(
        specimen_id="x", original_atoms=["a"], edited_atoms=["a", "b"],
        added=["b"], human_tuned=True,
    )
    assert "+1 added" in add.short_summary()
    assert "b" in add.short_summary()


def test_specimen_id_carries_through():
    dag = _dag([("s1", "atom.db.postgres.v1")])
    yaml_text = _compile_yaml(dag)
    diff = ReverseCompiler().diff("sp-xyz-007", dag, yaml_text)
    assert diff.specimen_id == "sp-xyz-007"
