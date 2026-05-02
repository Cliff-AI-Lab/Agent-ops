"""End-to-end test: FactorySessionImpl drives full 6-stage pipeline."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.llm.client import ChatMessage
from app.core.orchestrator.factory_session import (
    FactorySessionState,
    is_gate,
)
from app.core.orchestrator.factory_session.impl import FactorySessionImpl
from app.core.orchestrator.factory_session.persistence import SessionPersistence
from app.core.pipelines.factory.compiler import DifyCompilerImpl
from app.core.pipelines.factory.intent_parser import IntentParserImpl
from app.core.pipelines.factory.pipeline import FactoryPipeline
from app.core.pipelines.factory.resolver import ResolverImpl
from app.db.init_db import init_db
from app.registry.atom_loader import AtomLoaderImpl
from app.registry.search_engine import SearchEngineImpl


REPO_ROOT = Path(__file__).resolve().parents[6]
ATOMS_DIR = REPO_ROOT / "capabilities" / "atom"


def _intent_payload() -> dict:
    return {
        "schema_version": "1.0",
        "goal": "test goal",
        "trigger": {"type": "cron", "cron_expr": "0 9 * * 1"},
        "steps": [
            {
                "id": "s1",
                "verb": "查询数据库",
                "expected_output_kind": "tabular_data",
                "suggested_subcategory": "DB",
            },
            {
                "id": "s2",
                "verb": "撰写中文报",
                "expected_output_kind": "natural_language",
                "suggested_subcategory": "LLM",
                "inputs": {"data": "$s1.output"},
            },
            {
                "id": "s3",
                "verb": "推送钉钉",
                "expected_output_kind": "void",
                "suggested_subcategory": "Notify",
                "inputs": {"content": "$s2.output"},
            },
        ],
        "outputs": [{"name": "out", "type": "void"}],
        "raw_user_input": "测试 NL",
    }


def _rank_payload() -> dict:
    return {
        "asset_id": "atom.llm.chat.v1",
        "confidence": 0.9,
        "reason": "mocked",
        "llm_task_type": "长文档分块总结",
        "llm_size": "中",
    }


@pytest.fixture
async def temp_db(tmp_path):
    db_path = tmp_path / "test_factory.db"
    await init_db(db_path=str(db_path))
    return str(db_path)


@pytest.fixture
def pipeline_with_mock_llm():
    if not ATOMS_DIR.exists():
        pytest.skip(f"atoms dir missing: {ATOMS_DIR}")
    atoms = AtomLoaderImpl().load_all(ATOMS_DIR)
    search = SearchEngineImpl()
    search.index(atoms.values())
    compiler = DifyCompilerImpl()
    compiler.index(atoms)

    llm = MagicMock()
    intent_response = ChatMessage(role="assistant", content=json.dumps(_intent_payload()))
    rank_response = ChatMessage(role="assistant", content=json.dumps(_rank_payload()))
    llm.chat = AsyncMock(
        side_effect=[
            intent_response,
            rank_response,
            rank_response,
            rank_response,
        ]
    )
    router = MagicMock()
    router.resolve = MagicMock(return_value="test-model")

    return FactoryPipeline(
        intent_parser=IntentParserImpl(llm_client=llm, model_router=router),
        resolver=ResolverImpl(search_engine=search, llm_client=llm, model_router=router),
        compiler=compiler,
    )


@pytest.mark.asyncio
async def test_session_lifecycle_full_happy_path(temp_db, pipeline_with_mock_llm, tmp_path, monkeypatch):
    """Full happy path: create -> run all 6 stages with pass at each gate."""
    monkeypatch.chdir(tmp_path)  # ensure agents/__generated__/ writes are sandboxed

    persistence = SessionPersistence(db_path=temp_db)
    session = await FactorySessionImpl.create(
        pipeline=pipeline_with_mock_llm,
        persistence=persistence,
        nl="每周一上午9点 销售周报推钉钉",
    )
    assert session.state == FactorySessionState.CREATED

    # Stage 1: design
    new_state = await session.run_next_stage()
    assert new_state == FactorySessionState.GATE_DESIGN
    assert is_gate(new_state)

    # pass through all 6 gates
    for expected_next in [
        FactorySessionState.WRAPPING,
        FactorySessionState.ASSEMBLING,
        FactorySessionState.TESTING,
        FactorySessionState.UI_STYLING,
        FactorySessionState.DEPLOYING,
        FactorySessionState.RELEASED,
    ]:
        await session.apply_gate_decision("pass")
        if expected_next != FactorySessionState.RELEASED:
            new_state = await session.run_next_stage()
            assert is_gate(new_state)
        else:
            assert session.state == FactorySessionState.RELEASED


@pytest.mark.asyncio
async def test_session_redo_at_design_gate(temp_db, pipeline_with_mock_llm, tmp_path, monkeypatch):
    """Redo at GATE_DESIGN goes back to DESIGNING and reruns."""
    monkeypatch.chdir(tmp_path)
    persistence = SessionPersistence(db_path=temp_db)
    session = await FactorySessionImpl.create(
        pipeline=pipeline_with_mock_llm, persistence=persistence, nl="测试 redo"
    )

    await session.run_next_stage()
    assert session.state == FactorySessionState.GATE_DESIGN

    # redo: re-fixture LLM mocks since we'll call again
    llm = pipeline_with_mock_llm._intent_parser._llm
    llm.chat.side_effect = [
        ChatMessage(role="assistant", content=json.dumps(_intent_payload())),
        ChatMessage(role="assistant", content=json.dumps(_rank_payload())),
        ChatMessage(role="assistant", content=json.dumps(_rank_payload())),
        ChatMessage(role="assistant", content=json.dumps(_rank_payload())),
    ]
    await session.apply_gate_decision("redo", payload={"reason": "want different atoms"})
    assert session.state == FactorySessionState.DESIGNING

    new_state = await session.run_next_stage()
    assert new_state == FactorySessionState.GATE_DESIGN


@pytest.mark.asyncio
async def test_session_cancel(temp_db, pipeline_with_mock_llm, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    persistence = SessionPersistence(db_path=temp_db)
    session = await FactorySessionImpl.create(
        pipeline=pipeline_with_mock_llm, persistence=persistence, nl="测试 cancel"
    )
    await session.run_next_stage()
    assert session.state == FactorySessionState.GATE_DESIGN

    await session.cancel(reason="user changed mind")
    assert session.state == FactorySessionState.CANCELLED


@pytest.mark.asyncio
async def test_persistence_load_resume(temp_db, pipeline_with_mock_llm, tmp_path, monkeypatch):
    """A session can be loaded by id from another impl instance."""
    monkeypatch.chdir(tmp_path)
    persistence = SessionPersistence(db_path=temp_db)
    s1 = await FactorySessionImpl.create(
        pipeline=pipeline_with_mock_llm, persistence=persistence, nl="测试 load"
    )
    sid = s1.session_id
    await s1.run_next_stage()
    assert s1.state == FactorySessionState.GATE_DESIGN

    s2 = await FactorySessionImpl.load(
        pipeline=pipeline_with_mock_llm, persistence=persistence, session_id=sid
    )
    assert s2 is not None
    assert s2.state == FactorySessionState.GATE_DESIGN
    assert s2.session_id == sid


@pytest.mark.asyncio
async def test_persistence_records_artifacts_and_decisions(
    temp_db, pipeline_with_mock_llm, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    persistence = SessionPersistence(db_path=temp_db)
    session = await FactorySessionImpl.create(
        pipeline=pipeline_with_mock_llm, persistence=persistence, nl="测试 artifacts"
    )
    await session.run_next_stage()
    await session.apply_gate_decision("pass")

    design_artifact = await persistence.latest_artifact(session.session_id, "design")
    assert design_artifact is not None
    artifact_id, run_id, payload = design_artifact
    assert "intent" in payload
    assert "dag" in payload

    decisions = await persistence.list_gate_decisions(session.session_id)
    assert len(decisions) == 1
    assert decisions[0]["gate_id"] == "design"
    assert decisions[0]["decision"] == "pass"


@pytest.mark.asyncio
async def test_run_next_stage_at_gate_raises(temp_db, pipeline_with_mock_llm, tmp_path, monkeypatch):
    """Calling run_next_stage at a Gate is illegal; must apply decision first."""
    monkeypatch.chdir(tmp_path)
    persistence = SessionPersistence(db_path=temp_db)
    session = await FactorySessionImpl.create(
        pipeline=pipeline_with_mock_llm, persistence=persistence, nl="测试"
    )
    await session.run_next_stage()
    assert is_gate(session.state)
    with pytest.raises(RuntimeError, match="apply_gate_decision"):
        await session.run_next_stage()


# ----- Phase 5: multi-mode (设计 / 变体 / 量产) -----


@pytest.mark.asyncio
async def test_design_mode_default_does_not_auto_pass(
    temp_db, pipeline_with_mock_llm, tmp_path, monkeypatch
):
    """Default mode='design': run_to_next_human_gate stops at first gate."""
    monkeypatch.chdir(tmp_path)
    persistence = SessionPersistence(db_path=temp_db)
    session = await FactorySessionImpl.create(
        pipeline=pipeline_with_mock_llm, persistence=persistence, nl="测试 design 模式"
    )
    state = await session.run_to_next_human_gate()
    assert state == FactorySessionState.GATE_DESIGN  # stops at first gate


@pytest.mark.asyncio
async def test_variant_mode_skips_gates_1_2_3_stops_at_test(
    temp_db, pipeline_with_mock_llm, tmp_path, monkeypatch
):
    """variant mode: gates 1-3 auto-pass, stops at gate 4 (test) for human."""
    monkeypatch.chdir(tmp_path)
    persistence = SessionPersistence(db_path=temp_db)
    session = await FactorySessionImpl.create(
        pipeline=pipeline_with_mock_llm,
        persistence=persistence,
        nl="测试 variant 模式",
        mode="variant",
    )
    state = await session.run_to_next_human_gate()
    assert state == FactorySessionState.GATE_TEST

    # 3 gate_decisions recorded, all pass, decided_by=auto:variant
    decisions = await persistence.list_gate_decisions(session.session_id)
    assert len(decisions) == 3
    assert all(d["decision"] == "pass" for d in decisions)
    assert [d["gate_id"] for d in decisions] == ["design", "wrap", "assemble"]


@pytest.mark.asyncio
async def test_production_mode_runs_to_release(
    temp_db, pipeline_with_mock_llm, tmp_path, monkeypatch
):
    """production mode: all 6 gates auto-pass, ends at RELEASED."""
    monkeypatch.chdir(tmp_path)
    persistence = SessionPersistence(db_path=temp_db)
    session = await FactorySessionImpl.create(
        pipeline=pipeline_with_mock_llm,
        persistence=persistence,
        nl="测试 production 模式",
        mode="production",
    )
    state = await session.run_to_next_human_gate()
    assert state == FactorySessionState.RELEASED

    decisions = await persistence.list_gate_decisions(session.session_id)
    assert len(decisions) == 6
    assert [d["gate_id"] for d in decisions] == [
        "design", "wrap", "assemble", "test", "ui", "deploy"
    ]


@pytest.mark.asyncio
async def test_invalid_mode_rejected(temp_db, pipeline_with_mock_llm, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    persistence = SessionPersistence(db_path=temp_db)
    with pytest.raises(ValueError, match="mode must be"):
        await FactorySessionImpl.create(
            pipeline=pipeline_with_mock_llm,
            persistence=persistence,
            nl="测试",
            mode="bogus",
        )
