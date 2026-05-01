"""Tests for SearchEngine V1 (lexical)."""

from pathlib import Path

import pytest

from app.registry.atom_loader import AtomLoaderImpl
from app.registry.search_engine import SearchEngine, SearchEngineImpl


@pytest.fixture(scope="module")
def loaded_atoms() -> dict:
    repo_root = Path(__file__).resolve().parents[5]
    base = repo_root / "capabilities" / "atom"
    if not base.exists():
        pytest.skip(f"no atom dir: {base}")
    loader = AtomLoaderImpl()
    return loader.load_all(base)


@pytest.fixture
def engine(loaded_atoms) -> SearchEngineImpl:
    se = SearchEngineImpl()
    se.index(loaded_atoms.values())
    return se


def test_interface_importable():
    assert SearchEngine is not None


def test_empty_query_returns_empty(engine):
    assert engine.search_atoms("") == []
    assert engine.search_atoms("   ") == []


def test_index_is_indexable():
    se = SearchEngineImpl()
    se.index([])
    assert se.search_atoms("anything") == []


def test_search_finds_postgres_for_db_query(engine):
    """'数据库查询' should rank atom.db.postgres.v1 highest."""
    results = engine.search_atoms("数据库 查询 SQL", top_k=3)
    assert len(results) >= 1
    top_atom, top_score = results[0]
    assert top_atom.asset_id == "atom.db.postgres.v1"
    assert top_score > 0


def test_search_finds_dingtalk_for_notification(engine):
    """'钉钉 推送' should rank notify.dingtalk highest."""
    results = engine.search_atoms("钉钉 推送 通知", top_k=3)
    assert len(results) >= 1
    top_atom, _ = results[0]
    assert top_atom.asset_id == "atom.notify.dingtalk.v1"


def test_search_finds_cron_for_schedule(engine):
    results = engine.search_atoms("cron 定时", top_k=3)
    assert len(results) >= 1
    assert results[0][0].asset_id == "atom.schedule.cron.v1"


def test_subcategory_hard_filter(engine):
    """subcategory='HTTP' restricts results to HTTP atoms."""
    results = engine.search_atoms("API 请求", top_k=5, subcategory="HTTP")
    assert all(a.subcategory == "HTTP" for a, _ in results)


def test_subcategory_returns_empty_when_no_match(engine):
    results = engine.search_atoms("anything", top_k=5, subcategory="NONEXISTENT")
    assert results == []


def test_health_hard_filter_excludes_red(loaded_atoms):
    """Atoms with health=red must NOT appear in results, even relevant ones."""
    from copy import deepcopy
    from app.registry.atom_loader.models import HealthCheck

    atoms = {k: deepcopy(v) for k, v in loaded_atoms.items()}
    pg = atoms["atom.db.postgres.v1"]
    pg.health_check = HealthCheck(status="red")

    se = SearchEngineImpl()
    se.index(atoms.values())
    results = se.search_atoms("数据库 查询 SQL", top_k=5)
    asset_ids = [a.asset_id for a, _ in results]
    assert "atom.db.postgres.v1" not in asset_ids


def test_top_k_respected(engine):
    results = engine.search_atoms("查询", top_k=2)
    assert len(results) <= 2


def test_results_sorted_desc(engine):
    results = engine.search_atoms("API HTTP 请求", top_k=5)
    scores = [s for _, s in results]
    assert scores == sorted(scores, reverse=True)
