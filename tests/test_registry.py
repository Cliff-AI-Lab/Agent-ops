"""Tests for Agent Ops Capability Registry (Batch C · blueprint M2)."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.ontology import CapabilityContract
from app.registry import (
    CapabilityStore,
    bootstrap_registry,
    load_capabilities_from_dir,
    load_capability_from_yaml,
    reset_store,
    resolve_schema_ref,
)
from app.registry.bootstrap import default_capabilities_dir


# ---------- ref resolver ----------

def test_resolve_ontology_ref_returns_json_schema() -> None:
    schema = resolve_schema_ref("ontology:RequirementSpec")
    assert "properties" in schema
    assert "product_name" in schema["properties"]


def test_resolve_pipeline_ref_is_lazy_and_resolvable() -> None:
    schema = resolve_schema_ref("pipeline:Phase2Output")
    assert "properties" in schema
    assert "variants" in schema["properties"]


def test_resolve_unknown_ref_returns_placeholder() -> None:
    schema = resolve_schema_ref("ontology:NotAThing")
    assert schema.get("$comment", "").startswith("unresolved ref")


def test_resolve_malformed_ref_does_not_raise() -> None:
    schema = resolve_schema_ref("garbage")
    assert "malformed ref" in schema.get("$comment", "")


# ---------- YAML loader ----------

def test_load_single_yaml(tmp_path: Path) -> None:
    yaml_text = """
capability_id: demo.noop
version: 0.1.0
name: Demo Noop
description: |
  Echoes input verbatim.
owner: test
input_schema_ref: ontology:RequirementSpec
output_schema_ref: ontology:RequirementSpec
risk_level: low
activation_status: active
"""
    path = tmp_path / "demo.yaml"
    path.write_text(yaml_text, encoding="utf-8")
    contract = load_capability_from_yaml(path)
    assert contract.capability_id == "demo.noop"
    assert "product_name" in contract.input_schema.get("properties", {})
    assert contract.activation_status == "active"


def test_load_real_capabilities_directory() -> None:
    contracts = load_capabilities_from_dir(default_capabilities_dir())
    ids = sorted(c.capability_id for c in contracts)
    expected_subset = {
        "delivery.package_zip",
        "dialog.receptionist_turn",
        "ui.generate_production_code",
        "ui.generate_prototypes",
        "ui.plan_blueprint",
        "code_graph.parse",
    }
    assert expected_subset.issubset(set(ids)), f"missing: {expected_subset - set(ids)}"
    # Every contract has non-empty input/output schemas
    for c in contracts:
        assert isinstance(c.input_schema, dict) and c.input_schema
        assert isinstance(c.output_schema, dict) and c.output_schema


def test_malformed_yaml_is_skipped_not_raised(tmp_path: Path) -> None:
    (tmp_path / "bad.yaml").write_text("not: a: capability\n", encoding="utf-8")
    (tmp_path / "good.yaml").write_text(
        """
capability_id: x.y
version: 0.1.0
name: x
description: y
owner: z
input_schema_ref: ontology:RequirementSpec
output_schema_ref: ontology:RequirementSpec
risk_level: low
activation_status: active
""",
        encoding="utf-8",
    )
    contracts = load_capabilities_from_dir(tmp_path)
    ids = [c.capability_id for c in contracts]
    assert ids == ["x.y"]  # malformed is silently skipped


# ---------- CapabilityStore ----------

def _mk(capability_id: str, version: str, status: str = "active") -> CapabilityContract:
    return CapabilityContract(
        capability_id=capability_id, version=version,
        name=f"{capability_id}@{version}", description="…",
        owner="test", input_schema={"type": "object"}, output_schema={"type": "object"},
        risk_level="low", activation_status=status,
    )


def test_store_register_and_get_latest_active() -> None:
    s = CapabilityStore()
    s.register(_mk("a", "0.1.0"))
    s.register(_mk("a", "0.2.0"))
    s.register(_mk("a", "0.3.0", status="deprecated"))
    # Latest active should be 0.2.0
    c = s.get("a")
    assert c is not None and c.version == "0.2.0"
    # Exact-version fetch still works
    assert s.get("a", "0.3.0").activation_status == "deprecated"
    assert s.get("missing") is None


def test_store_list_filters_by_status_and_sorts() -> None:
    s = CapabilityStore()
    s.register(_mk("b", "0.1.0"))
    s.register(_mk("a", "0.1.0"))
    s.register(_mk("a", "0.2.0", status="draft"))
    active = s.list("active")
    assert [c.capability_id for c in active] == ["a", "b"]
    draft = s.list("draft")
    assert [c.version for c in draft] == ["0.2.0"]


def test_store_diff() -> None:
    s = CapabilityStore()
    s.register(_mk("a", "0.1.0"))
    mutated = _mk("a", "0.2.0")
    mutated.cost_budget = 1.5
    s.register(mutated)
    diff = s.diff("a", "0.1.0", "0.2.0")
    assert "cost_budget" in diff
    assert diff["cost_budget"]["v2"] == 1.5


# ---------- bootstrap (global singleton) ----------

def test_bootstrap_loads_real_capabilities_from_project_dir() -> None:
    reset_store()
    n = bootstrap_registry()
    # 5 baseline (Sprint 1) + 1 added in Batch C+++ (code_graph.parse)
    assert n >= 6
    from app.registry.store import get_store
    ids = get_store().list_ids()
    assert "ui.plan_blueprint" in ids
    assert "dialog.receptionist_turn" in ids
    assert "code_graph.parse" in ids
    assert "delivery.package_zip" in ids
