"""Tests for DependencyGraph (V2.4 W1)."""
from __future__ import annotations

import pytest

from app.registry.dependency_graph import (
    DependencyEdge,
    DependencyGraphImpl,
    build_graph_from_atoms_and_prompts,
)


def _e(s: str, t: str, kind: str = "references_prompt") -> DependencyEdge:
    return DependencyEdge(source_id=s, target_id=t, kind=kind)


def test_add_edge_records_both_directions():
    g = DependencyGraphImpl()
    g.add_edge(_e("atom.llm.chat.v1", "prompt.report.zh.v1"))
    assert g.depends_on("atom.llm.chat.v1") == ["prompt.report.zh.v1"]
    assert g.dependents_of("prompt.report.zh.v1") == ["atom.llm.chat.v1"]


def test_self_loop_rejected():
    g = DependencyGraphImpl()
    with pytest.raises(ValueError, match="self-loop"):
        g.add_edge(_e("a", "a"))


def test_dedup_edges():
    g = DependencyGraphImpl()
    g.add_edge(_e("a", "b"))
    g.add_edge(_e("a", "b"))
    assert g.depends_on("a") == ["b"]
    assert g.dependents_of("b") == ["a"]


def test_impact_transitive_closure():
    """a -> b -> c -> d; impact_of(d) returns {a, b, c}."""
    g = DependencyGraphImpl()
    g.add_edge(_e("a", "b"))
    g.add_edge(_e("b", "c"))
    g.add_edge(_e("c", "d"))
    report = g.impact_of("d")
    assert report.root_asset_id == "d"
    assert report.direct_dependents == ["c"]
    assert set(report.all_dependents) == {"a", "b", "c"}


def test_impact_diamond_dedup():
    """Diamond: a -> {b, c}; b -> d; c -> d. impact_of(d) = {a, b, c} no dup."""
    g = DependencyGraphImpl()
    g.add_edge(_e("b", "a"))
    g.add_edge(_e("c", "a"))
    g.add_edge(_e("d", "b"))
    g.add_edge(_e("d", "c"))
    report = g.impact_of("a")
    assert set(report.all_dependents) == {"b", "c", "d"}


def test_no_dependents_returns_empty_report():
    g = DependencyGraphImpl()
    g.add_edge(_e("a", "b"))
    report = g.impact_of("c")  # not in graph
    assert report.direct_dependents == []
    assert report.all_dependents == []


def test_has_cycle_detects():
    g = DependencyGraphImpl()
    g.add_edge(_e("a", "b"))
    g.add_edge(_e("b", "c"))
    assert g.has_cycle() is False
    g.add_edge(_e("c", "a"))
    assert g.has_cycle() is True


def test_all_assets_includes_isolated():
    g = DependencyGraphImpl()
    g.add_node("orphan")
    g.add_edge(_e("a", "b"))
    assert set(g.all_assets()) == {"orphan", "a", "b"}


# ---- builder test ---------------------------------------------------------


class _FakeAtom:
    def __init__(self, prompt_default: str | None) -> None:
        # io_schema is dict-like in production; mirror that here
        if prompt_default:
            self.io_schema = type("IOS", (), {
                "inputs": {
                    "properties": {
                        "prompt_id": {"default": prompt_default}
                    }
                }
            })()
        else:
            self.io_schema = type("IOS", (), {"inputs": {}})()


class _FakePrompt:
    pass


def test_builder_creates_atom_to_prompt_edge():
    atoms = {
        "atom.llm.chat.v1": _FakeAtom(prompt_default="prompt.report.zh.v1"),
    }
    prompts = {"prompt.report.zh.v1": _FakePrompt()}
    g = build_graph_from_atoms_and_prompts(atoms, prompts)
    assert g.depends_on("atom.llm.chat.v1") == ["prompt.report.zh.v1"]
    assert "atom.llm.chat.v1" in g.dependents_of("prompt.report.zh.v1")


def test_builder_skips_atom_with_no_prompt_default():
    atoms = {"atom.db.postgres.v1": _FakeAtom(prompt_default=None)}
    prompts = {}
    g = build_graph_from_atoms_and_prompts(atoms, prompts)
    # Atom registered but no edge to anything
    assert g.depends_on("atom.db.postgres.v1") == []


def test_builder_skips_unknown_prompt_default():
    atoms = {"atom.llm.chat.v1": _FakeAtom(prompt_default="prompt.MISSING.v1")}
    prompts = {}  # default ID not in registry
    g = build_graph_from_atoms_and_prompts(atoms, prompts)
    assert g.depends_on("atom.llm.chat.v1") == []
