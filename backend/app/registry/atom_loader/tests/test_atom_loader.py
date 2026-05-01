"""Tests for AtomLoader."""

from pathlib import Path

import pytest

from app.registry.atom_loader import AtomDef, AtomLoaderImpl


def test_loader_constructable():
    loader = AtomLoaderImpl()
    assert loader is not None


def test_load_postgres_seed_atom():
    """Phase 1 seed atom must load and validate."""
    repo_root = Path(__file__).resolve().parents[5]
    yaml_path = repo_root / "capabilities" / "atom" / "db" / "postgres.v1.yaml"
    if not yaml_path.exists():
        pytest.skip(f"seed atom not present: {yaml_path}")
    loader = AtomLoaderImpl()
    atom = loader.load_one(yaml_path)
    assert isinstance(atom, AtomDef)
    assert atom.asset_id == "atom.db.postgres.v1"
    assert atom.subcategory == "DB"
    assert atom.layer == "L2"
    assert len(atom.NOT_applicable) >= 1
    assert len(atom.test_cases) >= 2
    assert "dify" in atom.projections


def test_load_all_atoms_under_capabilities():
    repo_root = Path(__file__).resolve().parents[5]
    base = repo_root / "capabilities" / "atom"
    if not base.exists():
        pytest.skip(f"atom dir missing: {base}")
    loader = AtomLoaderImpl()
    atoms = loader.load_all(base)
    assert len(atoms) >= 1


def test_invalid_asset_id_rejected():
    """asset_id pattern is enforced."""
    from app.registry.atom_loader.models import (
        AtomDef,
        IOSchema,
        Metrics,
        ProjectionDef,
        Provenance,
        TestCase,
    )

    base_kwargs = dict(
        schema_version="1.0",
        display_id="AT-999",
        subcategory="DB",
        version="1.0.0",
        name="bad",
        description="x" * 30,
        tags=["x"],
        NOT_applicable=["x"],
        maintainer="me / 2026-05",
        io_schema=IOSchema(inputs={}, outputs={}),
        projections={"dify": ProjectionDef()},
        test_cases=[
            TestCase(name="happy"),
            TestCase(name="edge"),
        ],
        metrics=Metrics(),
        provenance=Provenance(built_at="2026-05-01", built_by="manual"),
    )
    with pytest.raises(Exception):
        AtomDef(asset_id="bad-format", **base_kwargs)
