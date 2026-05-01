"""Marketplace + Asset Hub tests (Batch X)."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from app.marketplace.asset_store import AssetStore
from app.marketplace.auto_register import auto_register, reload_generated_agents
from app.ontology import (
    AgentContract,
    BrandTokens,
    CodeArtifact,
    FileEntry,
    GeneratedAsset,
    PageBlueprint,
    RequirementSpec,
    UIBlueprint,
)


@pytest.fixture
def tmp_store(tmp_path) -> AssetStore:
    """Isolated store backed by a temp SQLite db."""
    db = tmp_path / "test.db"
    return AssetStore(db_path=db)


@pytest.fixture
def sample_artifact() -> CodeArtifact:
    return CodeArtifact(
        files=[
            FileEntry(path="src/main.tsx", content="export default function() { return null; }"),
            FileEntry(path="src/App.tsx", content="// app"),
            FileEntry(path="package.json", content='{"name":"x","version":"0.1.0"}'),
        ],
        dependencies={"react": "^18"},
        entrypoint="src/main.tsx",
    )


@pytest.fixture
def sample_spec() -> RequirementSpec:
    return RequirementSpec(
        product_name="DemoApp",
        product_type="app",
        target_users=["team"],
        core_pages=["home"],
    )


# ---------- AssetStore CRUD ----------
def test_register_and_get(tmp_store: AssetStore):
    a = GeneratedAsset(
        asset_id="demo-1",
        name="Demo",
        artifact_path="assets/demo-1/project.zip",
        generated_at=datetime.utcnow(),
    )
    tmp_store.register(a)
    fetched = tmp_store.get("demo-1")
    assert fetched is not None
    assert fetched.asset_id == "demo-1"
    assert fetched.status == "draft"


def test_list_filters(tmp_store: AssetStore):
    for i, status in enumerate(["draft", "active", "active", "archived"]):
        tmp_store.register(GeneratedAsset(
            asset_id=f"a{i}", name=f"a{i}", artifact_path=f"x{i}",
            generated_at=datetime.utcnow(), status=status,
        ))
    assert len(tmp_store.list()) == 4
    assert len(tmp_store.list(status="active")) == 2
    assert len(tmp_store.list(status="archived")) == 1


def test_promote_changes_status(tmp_store: AssetStore):
    tmp_store.register(GeneratedAsset(
        asset_id="x", name="x", artifact_path="x",
        generated_at=datetime.utcnow(),
    ))
    promoted = tmp_store.promote("x", promoted_by="alice")
    assert promoted is not None
    assert promoted.status == "active"
    assert promoted.promoted_by == "alice"
    assert promoted.promoted_at is not None
    re_get = tmp_store.get("x")
    assert re_get is not None and re_get.status == "active"


def test_archive(tmp_store: AssetStore):
    tmp_store.register(GeneratedAsset(
        asset_id="y", name="y", artifact_path="y",
        generated_at=datetime.utcnow(), status="active",
    ))
    tmp_store.archive("y")
    assert tmp_store.get("y").status == "archived"


def test_increment_usage(tmp_store: AssetStore):
    tmp_store.register(GeneratedAsset(
        asset_id="z", name="z", artifact_path="z",
        generated_at=datetime.utcnow(),
    ))
    tmp_store.increment_usage("z")
    tmp_store.increment_usage("z")
    a = tmp_store.get("z")
    assert a.usage_count == 2


def test_stats(tmp_store: AssetStore):
    for i, status in enumerate(["draft", "draft", "active"]):
        tmp_store.register(GeneratedAsset(
            asset_id=f"s{i}-{status}", name="x", artifact_path="x",
            generated_at=datetime.utcnow(), status=status,
        ))
    s = tmp_store.stats()
    assert s.get("total", 0) >= 3
    assert s.get("draft", 0) >= 2
    assert s.get("active", 0) >= 1


# ---------- auto_register ----------
def test_auto_register_writes_zip_and_yaml(tmp_path, monkeypatch, sample_artifact, sample_spec):
    """auto_register should drop a zip + agent.yaml on disk and create a draft row."""
    assets_root = tmp_path / "assets"
    agents_root = tmp_path / "agents" / "__generated__"
    db_path = tmp_path / "test.db"

    # Override the singleton store path
    from app.marketplace import asset_store as as_mod
    as_mod._asset_store = None  # noqa: SLF001
    monkeypatch.setattr(as_mod, "default_db_path", lambda: db_path)

    asset = auto_register(
        sample_artifact, sample_spec,
        run_id="run-abc123",
        source_session_id="sess-xyz",
        source_sop="DemoApp 一句话",
        assets_root=assets_root,
        generated_agents_root=agents_root,
    )

    # 1) Asset row persisted
    assert asset.asset_id.startswith("demoapp-")
    assert asset.status == "draft"
    assert asset.file_count == len(sample_artifact.files)
    assert asset.total_bytes > 0
    assert "generated" in asset.tags

    # 2) Files on disk
    zip_path = assets_root / asset.asset_id / "project.zip"
    assert zip_path.is_file()
    assert zip_path.stat().st_size == asset.total_bytes
    yaml_path = agents_root / asset.asset_id / "agent.yaml"
    assert yaml_path.is_file()
    yaml_content = yaml_path.read_text(encoding="utf-8")
    assert "agent_id" in yaml_content and "gen." in yaml_content


def test_auto_register_idempotent(tmp_path, monkeypatch, sample_artifact, sample_spec):
    """Re-registering with the same run_id replaces the row in place."""
    assets_root = tmp_path / "assets"
    agents_root = tmp_path / "gen_agents"
    db_path = tmp_path / "test.db"

    from app.marketplace import asset_store as as_mod
    as_mod._asset_store = None  # noqa: SLF001
    monkeypatch.setattr(as_mod, "default_db_path", lambda: db_path)

    a1 = auto_register(sample_artifact, sample_spec, run_id="r1",
                       assets_root=assets_root, generated_agents_root=agents_root)
    a2 = auto_register(sample_artifact, sample_spec, run_id="r1",
                       assets_root=assets_root, generated_agents_root=agents_root)
    assert a1.asset_id == a2.asset_id
    # Only one row in store
    rows = as_mod.get_asset_store().list()
    asset_ids = [r.asset_id for r in rows if r.asset_id == a1.asset_id]
    assert len(asset_ids) == 1


def test_reload_generated_agents_only_loads_active(tmp_path, monkeypatch, sample_artifact, sample_spec):
    """reload should only register agents whose backing asset is 'active'."""
    assets_root = tmp_path / "assets"
    agents_root = tmp_path / "gen_agents"
    db_path = tmp_path / "test.db"

    from app.marketplace import asset_store as as_mod
    as_mod._asset_store = None  # noqa: SLF001
    monkeypatch.setattr(as_mod, "default_db_path", lambda: db_path)
    monkeypatch.setattr(as_mod, "default_assets_root", lambda: assets_root)
    monkeypatch.setattr(as_mod, "default_generated_agents_dir", lambda: agents_root)

    from app.registry import agent_store as ags_mod
    ags_mod.reset_agent_store()

    # Two assets; one promoted, one draft
    a1 = auto_register(sample_artifact, sample_spec, run_id="active-1",
                       assets_root=assets_root, generated_agents_root=agents_root)
    a2 = auto_register(sample_artifact, sample_spec, run_id="draft-1",
                       assets_root=assets_root, generated_agents_root=agents_root)
    as_mod.get_asset_store().promote(a1.asset_id)

    # Patch the auto_register module's helpers so reload uses our temp paths
    from app.marketplace import auto_register as ar_mod
    monkeypatch.setattr(ar_mod, "default_generated_agents_dir", lambda: agents_root)

    n = reload_generated_agents()
    assert n == 1
    store = ags_mod.get_agent_store()
    assert store.get(f"gen.{a1.asset_id}") is not None
    assert store.get(f"gen.{a2.asset_id}") is None
