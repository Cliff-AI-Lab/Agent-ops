"""Tests for EvalRunner with mocked LLM (deterministic)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.llm.client import ChatMessage
from app.core.pipelines.factory.compiler import DifyCompilerImpl
from app.core.pipelines.factory.intent_parser import IntentParserImpl
from app.core.pipelines.factory.pipeline import FactoryPipeline
from app.core.pipelines.factory.resolver import ResolverImpl
from app.registry.atom_loader import AtomLoaderImpl
from app.registry.eval_runner import (
    EvalCase,
    EvalReport,
    EvalRunnerImpl,
    EvalSet,
    load_eval_set,
    load_eval_sets,
)
from app.registry.eval_runner.models import ExpectedShape
from app.registry.search_engine import SearchEngineImpl


REPO_ROOT = Path(__file__).resolve().parents[5]
ATOMS_DIR = REPO_ROOT / "capabilities" / "atom"
EVAL_DIR = REPO_ROOT / "capabilities" / "eval_set"


def _intent_payload(nl: str) -> dict:
    return {
        "schema_version": "1.0",
        "goal": "测试目标",
        "trigger": {"type": "cron", "cron_expr": "0 9 * * 1"},
        "steps": [
            {
                "id": "s1",
                "verb": "查询数据",
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
        "raw_user_input": nl,
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
def mock_pipeline_factory():
    """Returns a callable that builds a FactoryPipeline with deterministic LLM."""
    if not ATOMS_DIR.exists():
        pytest.skip(f"atoms dir missing: {ATOMS_DIR}")

    def _make() -> FactoryPipeline:
        atoms = AtomLoaderImpl().load_all(ATOMS_DIR)
        search = SearchEngineImpl()
        search.index(atoms.values())
        compiler = DifyCompilerImpl()
        compiler.index(atoms)

        llm = MagicMock()
        # Each .build() = 1 intent call + N rank calls (one per step).
        # Provide enough side-effects for typical 3-step intent.
        llm.chat = AsyncMock(
            side_effect=lambda **kwargs: ChatMessage(
                role="assistant",
                content=json.dumps(
                    _intent_payload("test")
                    if any(
                        "IntentParser" in m.content
                        for m in kwargs.get("messages", [])
                    )
                    else _rank_payload()
                ),
            )
        )
        router = MagicMock()
        router.resolve = MagicMock(return_value="test-model")
        return FactoryPipeline(
            intent_parser=IntentParserImpl(llm_client=llm, model_router=router),
            resolver=ResolverImpl(
                search_engine=search, llm_client=llm, model_router=router
            ),
            compiler=compiler,
        )

    return _make


def test_models_construct():
    case = EvalCase(
        case_id="c1",
        description="d",
        nl="测试需求字符串",
        expected=ExpectedShape(target="hybrid", subcategories=["DB", "LLM"]),
    )
    assert case.case_id == "c1"

    es = EvalSet(asset_id="ES-T", version="1.0", description="d", cases=[case])
    assert len(es.cases) == 1


def test_load_eval_set_yaml():
    if not EVAL_DIR.exists():
        pytest.skip(f"no eval_set dir: {EVAL_DIR}")
    sets = load_eval_sets(EVAL_DIR)
    assert len(sets) >= 1


@pytest.mark.asyncio
async def test_runner_pass_rate_on_real_set(mock_pipeline_factory):
    """Run the bundled MVP eval set with deterministic mocked LLM."""
    if not EVAL_DIR.exists():
        pytest.skip(f"no eval_set dir: {EVAL_DIR}")
    sets = load_eval_sets(EVAL_DIR)
    es = next(iter(sets.values()))

    pipeline = mock_pipeline_factory()
    runner = EvalRunnerImpl(pipeline=pipeline)
    report = await runner.run(es)

    assert isinstance(report, EvalReport)
    assert report.total == len(es.cases)
    assert report.pass_rate >= 0.0


@pytest.mark.asyncio
async def test_runner_records_per_case_failure_reason(mock_pipeline_factory):
    """A case expecting target=n8n should fail (we always emit hybrid)."""
    pipeline = mock_pipeline_factory()
    runner = EvalRunnerImpl(pipeline=pipeline)
    bad_case = EvalCase(
        case_id="impossible",
        description="expects n8n but we emit hybrid",
        nl="测试用例字符串",
        expected=ExpectedShape(target="n8n"),
    )
    es = EvalSet(
        asset_id="ES-T", version="1.0", description="d", cases=[bad_case]
    )
    report = await runner.run(es)
    assert report.passed == 0
    assert report.failed == 1
    res = report.case_results[0]
    assert any("target" in r for r in res.reasons)


def test_eval_report_thresholds():
    rpt = EvalReport(
        eval_set_id="x", total=10, passed=8, failed=2, pass_rate=0.8
    )
    assert rpt.is_mvp_threshold_met is True
    assert rpt.is_ga_threshold_met is True

    rpt2 = EvalReport(
        eval_set_id="x", total=10, passed=7, failed=3, pass_rate=0.7
    )
    assert rpt2.is_mvp_threshold_met is True
    assert rpt2.is_ga_threshold_met is False

    rpt3 = EvalReport(
        eval_set_id="x", total=10, passed=6, failed=4, pass_rate=0.6
    )
    assert rpt3.is_mvp_threshold_met is False
