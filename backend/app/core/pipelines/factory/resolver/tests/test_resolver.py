"""Tests for ResolverImpl + submodules with mocked LLM and real atoms."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.llm.client import ChatMessage
from app.core.pipelines.factory.ir import (
    OutputSpec,
    StepSpec,
    StructuredIntent,
    TriggerSpec,
)
from app.core.pipelines.factory.resolver import Resolver, ResolverImpl
from app.core.pipelines.factory.resolver.bind import (
    build_edges,
    build_node,
    select_target,
)
from app.core.pipelines.factory.resolver.rank import (
    _heuristic_llm_routing,
    rank_candidates,
)
from app.core.pipelines.factory.resolver.recall import recall_for_step
from app.registry.atom_loader import AtomLoaderImpl
from app.registry.search_engine import SearchEngineImpl


@pytest.fixture(scope="module")
def loaded_atoms():
    # backend/app/core/pipelines/factory/resolver/tests/test_resolver.py
    # parents[7] = agent-harness root
    repo_root = Path(__file__).resolve().parents[7]
    base = repo_root / "capabilities" / "atom"
    if not base.exists():
        pytest.skip(f"no atom dir: {base}")
    return AtomLoaderImpl().load_all(base)


@pytest.fixture
def search_engine(loaded_atoms):
    se = SearchEngineImpl()
    se.index(loaded_atoms.values())
    return se


def _mock_router(model: str = "test-model"):
    router = MagicMock()
    router.resolve = MagicMock(return_value=model)
    return router


def _llm_picking(asset_id: str, confidence: float = 0.9):
    """LLM returns a rank decision picking asset_id."""
    payload = {
        "asset_id": asset_id,
        "confidence": confidence,
        "reason": "mocked",
        "llm_task_type": None,
        "llm_size": None,
    }
    llm = MagicMock()
    llm.chat = AsyncMock(
        return_value=ChatMessage(role="assistant", content=json.dumps(payload))
    )
    return llm


def test_interface_importable():
    assert Resolver is not None


def test_recall_finds_postgres_for_db_step(search_engine):
    step = StepSpec(
        id="s1", verb="查询数据库", expected_output_kind="tabular_data",
        suggested_subcategory="DB",
    )
    cands = recall_for_step(step, search_engine)
    assert any(a.asset_id == "atom.db.postgres.v1" for a, _ in cands)


def test_recall_returns_empty_when_no_match(search_engine):
    step = StepSpec(
        id="s1", verb="完全不相关的需求", expected_output_kind="void",
        suggested_subcategory="NONEXISTENT",
    )
    cands = recall_for_step(step, search_engine)
    assert cands == []


@pytest.mark.asyncio
async def test_rank_with_single_candidate_no_llm(search_engine, loaded_atoms):
    step = StepSpec(id="s1", verb="cron 触发", expected_output_kind="void")
    pg = loaded_atoms["atom.db.postgres.v1"]
    candidates = [(pg, 0.9)]
    llm = MagicMock()
    sel = await rank_candidates(step, candidates, llm_client=llm, model="m")
    assert sel.atom == pg
    assert llm.chat.called is False


@pytest.mark.asyncio
async def test_rank_picks_llm_chosen_atom(loaded_atoms):
    step = StepSpec(id="s2", verb="撰写中文周报", expected_output_kind="natural_language")
    pg = loaded_atoms["atom.db.postgres.v1"]
    llm_atom = loaded_atoms["atom.llm.chat.v1"]
    candidates = [(pg, 0.5), (llm_atom, 0.3)]
    llm = _llm_picking("atom.llm.chat.v1", confidence=0.92)
    sel = await rank_candidates(step, candidates, llm_client=llm, model="m")
    assert sel.atom.asset_id == "atom.llm.chat.v1"
    assert sel.confidence == 0.92


@pytest.mark.asyncio
async def test_rank_falls_back_when_llm_picks_unknown(loaded_atoms):
    step = StepSpec(id="s2", verb="x", expected_output_kind="void")
    pg = loaded_atoms["atom.db.postgres.v1"]
    candidates = [(pg, 0.5)]
    payload = {"asset_id": "atom.unknown.v1", "confidence": 0.9, "reason": "x"}
    llm = MagicMock()
    llm.chat = AsyncMock(
        return_value=ChatMessage(role="assistant", content=json.dumps(payload))
    )
    candidates.append((loaded_atoms["atom.http.generic.v1"], 0.4))
    sel = await rank_candidates(step, candidates, llm_client=llm, model="m")
    assert sel.atom == pg


def test_heuristic_llm_routing():
    assert _heuristic_llm_routing("分析数据找异常") == ("长文档分块总结", "中")
    assert _heuristic_llm_routing("从合同抽取关键条款") == ("结构化抽取", "中")
    assert _heuristic_llm_routing("制定推进方案") == ("长上下文 / 深度推理", "大")
    assert _heuristic_llm_routing("润色标题") == ("轻量判别", "小")


def test_build_node_for_db_atom(loaded_atoms):
    step = StepSpec(id="s1", verb="查询", expected_output_kind="tabular_data")
    pg = loaded_atoms["atom.db.postgres.v1"]
    node = build_node(step, pg, confidence=0.9, reason="r")
    assert node.id == "s1"
    assert node.asset_id == "atom.db.postgres.v1"
    assert node.llm_task_type is None


def test_build_node_for_llm_carries_routing(loaded_atoms):
    step = StepSpec(id="s2", verb="撰写", expected_output_kind="natural_language")
    llm_atom = loaded_atoms["atom.llm.chat.v1"]
    node = build_node(
        step, llm_atom, confidence=0.9, reason="r",
        llm_task_type="长文档分块总结", llm_size="中",
    )
    assert node.llm_task_type == "长文档分块总结"
    assert node.llm_size == "中"
    assert "中" in node.llm_fallback_chain


def test_build_edges_with_type_check(loaded_atoms):
    intent = StructuredIntent(
        goal="g",
        trigger=TriggerSpec(type="manual"),
        steps=[
            StepSpec(id="s1", verb="查询", expected_output_kind="tabular_data"),
            StepSpec(
                id="s2",
                verb="推送",
                expected_output_kind="void",
                inputs={"content": "$s1.output"},
            ),
        ],
        outputs=[OutputSpec(name="o", type="void")],
        raw_user_input="x",
    )
    pg = loaded_atoms["atom.db.postgres.v1"]
    dingtalk = loaded_atoms["atom.notify.dingtalk.v1"]
    nodes = {
        "s1": build_node(intent.steps[0], pg, 0.9, "r"),
        "s2": build_node(intent.steps[1], dingtalk, 0.9, "r"),
    }
    edges = build_edges(intent, nodes, {pg.asset_id: pg, dingtalk.asset_id: dingtalk})
    assert len(edges) == 1
    e = edges[0]
    assert e.from_node == "s1" and e.to_node == "s2"
    assert e.type_check.from_type == "tabular_data"


def test_select_target_hybrid_when_llm_and_n8n_mixed(loaded_atoms):
    intent = StructuredIntent(
        goal="g",
        trigger=TriggerSpec(type="cron", cron_expr="0 9 * * 1"),
        steps=[],
        outputs=[],
        raw_user_input="x",
    )
    pg = loaded_atoms["atom.db.postgres.v1"]
    llm_atom = loaded_atoms["atom.llm.chat.v1"]
    nodes = {
        "s1": build_node(
            StepSpec(id="s1", verb="x", expected_output_kind="tabular_data"),
            pg, 0.9, "r"
        ),
        "s2": build_node(
            StepSpec(id="s2", verb="x", expected_output_kind="natural_language"),
            llm_atom, 0.9, "r"
        ),
    }
    target, split = select_target(intent, nodes, {pg.asset_id: pg, llm_atom.asset_id: llm_atom})
    assert target == "hybrid"
    assert "s1" in split["n8n_nodes"]
    assert "s2" in split["dify_nodes"]


@pytest.mark.asyncio
async def test_resolver_e2e_with_mocked_llm(search_engine):
    """End-to-end: real atoms, mocked LLM, full intent -> ResolvedDAG."""
    intent = StructuredIntent(
        goal="销售周报",
        trigger=TriggerSpec(type="cron", cron_expr="0 9 * * 1"),
        steps=[
            StepSpec(
                id="s1",
                verb="cron 定时触发",
                expected_output_kind="void",
                suggested_subcategory="Schedule",
            ),
            StepSpec(
                id="s2",
                verb="查询销售数据库",
                expected_output_kind="tabular_data",
                suggested_subcategory="DB",
                inputs={"trigger": "$s1.output"},
            ),
            StepSpec(
                id="s3",
                verb="撰写中文周报",
                expected_output_kind="natural_language",
                suggested_subcategory="LLM",
                inputs={"data": "$s2.output"},
            ),
            StepSpec(
                id="s4",
                verb="推送到钉钉",
                expected_output_kind="void",
                suggested_subcategory="Notify",
                inputs={"content": "$s3.output"},
            ),
        ],
        outputs=[OutputSpec(name="status", type="void", sink="dingtalk:运营群")],
        raw_user_input="每周一上午9点 销售周报推钉钉",
    )

    llm = MagicMock()
    llm.chat = AsyncMock(
        return_value=ChatMessage(
            role="assistant",
            content=json.dumps(
                {
                    "asset_id": "atom.llm.chat.v1",
                    "confidence": 0.9,
                    "reason": "mocked",
                    "llm_task_type": "长文档分块总结",
                    "llm_size": "中",
                }
            ),
        )
    )
    resolver = ResolverImpl(
        search_engine=search_engine, llm_client=llm, model_router=_mock_router()
    )
    dag = await resolver.resolve(intent)

    assert len(dag.nodes) == 4
    assert dag.target == "hybrid"
    asset_ids = {n.asset_id for n in dag.nodes}
    assert "atom.schedule.cron.v1" in asset_ids
    assert "atom.db.postgres.v1" in asset_ids
    assert "atom.llm.chat.v1" in asset_ids
    assert "atom.notify.dingtalk.v1" in asset_ids
    assert len(dag.edges) == 3  # s1->s2, s2->s3, s3->s4
    llm_node = next(n for n in dag.nodes if n.asset_id == "atom.llm.chat.v1")
    assert llm_node.llm_task_type == "长文档分块总结"
    assert llm_node.llm_size == "中"
