"""Tests for the `factory` CLI."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.cli import factory as factory_cli
from app.core.llm.client import ChatMessage


REPO_ROOT = Path(__file__).resolve().parents[1]
ATOMS_DIR = REPO_ROOT / "capabilities" / "atom"


def _intent_payload() -> dict:
    return {
        "schema_version": "1.0",
        "goal": "test",
        "trigger": {"type": "cron", "cron_expr": "0 9 * * 1"},
        "steps": [
            {
                "id": "s1",
                "verb": "查询销售数据库",
                "expected_output_kind": "tabular_data",
                "suggested_subcategory": "DB",
            },
            {
                "id": "s2",
                "verb": "撰写中文周报",
                "expected_output_kind": "natural_language",
                "suggested_subcategory": "LLM",
                "inputs": {"data": "$s1.output"},
            },
        ],
        "outputs": [{"name": "out", "type": "void"}],
        "raw_user_input": "test build",
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
def mock_llm():
    llm = MagicMock()
    llm.chat = AsyncMock(
        side_effect=[
            ChatMessage(role="assistant", content=json.dumps(_intent_payload())),
            ChatMessage(role="assistant", content=json.dumps(_rank_payload())),
            ChatMessage(role="assistant", content=json.dumps(_rank_payload())),
        ]
    )
    return llm


def test_cli_main_no_args_returns_2(capsys):
    with pytest.raises(SystemExit) as exc:
        factory_cli.main([])
    assert exc.value.code == 2


def test_cli_main_unknown_subcmd(capsys):
    with pytest.raises(SystemExit):
        factory_cli.main(["unknown"])


def test_cli_human_print_writes_summary(capsys):
    """_human_print: smoke test of the printer."""
    from app.core.pipelines.factory.ir import (
        OutputSpec,
        ResolvedDAG,
        StepSpec,
        StructuredIntent,
        TriggerSpec,
    )

    intent = StructuredIntent(
        goal="测试",
        trigger=TriggerSpec(type="cron", cron_expr="0 9 * * 1"),
        steps=[StepSpec(id="s1", verb="x", expected_output_kind="void")],
        outputs=[OutputSpec(name="o", type="void")],
        raw_user_input="x",
    )
    dag = ResolvedDAG(intent_ref="x", nodes=[], edges=[], target="dify", issues=[])
    factory_cli._human_print(
        {
            "intent": intent,
            "dag": dag,
            "validation": {"ok": True, "issues": []},
        }
    )
    out = capsys.readouterr().out
    assert "goal:" in out
    assert "测试" in out
    assert "valid:   ok" in out


@pytest.mark.asyncio
async def test_run_build_e2e_with_mocked_llm(mock_llm):
    """_run_build wires Pipeline + Validator end-to-end with mocked LLM."""
    if not ATOMS_DIR.exists():
        pytest.skip(f"atoms dir missing: {ATOMS_DIR}")

    with patch.object(factory_cli, "_atoms_dir", return_value=ATOMS_DIR), patch(
        "app.core.pipelines.factory.pipeline.LLMClient", return_value=mock_llm
    ), patch(
        "app.core.pipelines.factory.intent_parser.impl.LLMClient",
        return_value=mock_llm,
    ):
        from app.core.pipelines.factory.intent_parser import IntentParserImpl
        from app.core.pipelines.factory.pipeline import FactoryPipeline
        from app.core.pipelines.factory.resolver import ResolverImpl
        from app.core.pipelines.factory.validator import DSLValidatorImpl
        from app.registry.atom_loader import AtomLoaderImpl
        from app.registry.search_engine import SearchEngineImpl

        # Manual wiring to inject mock_llm precisely (env-independent).
        atoms = AtomLoaderImpl().load_all(ATOMS_DIR)
        search = SearchEngineImpl()
        search.index(atoms.values())
        router = MagicMock()
        router.resolve = MagicMock(return_value="test-model")
        pipeline = FactoryPipeline(
            intent_parser=IntentParserImpl(llm_client=mock_llm, model_router=router),
            resolver=ResolverImpl(
                search_engine=search, llm_client=mock_llm, model_router=router
            ),
        )
        # Ensure compiler has atoms indexed (FactoryPipeline default did so when atoms_dir provided)
        from app.core.pipelines.factory.compiler import DifyCompilerImpl

        compiler = DifyCompilerImpl()
        compiler.index(atoms)
        pipeline._compiler = compiler

        result = await pipeline.build("test build")
        validator = DSLValidatorImpl()
        # In hybrid mode, validate the dify slice; in single-target, use that target
        target_for_validation = (
            "dify" if "dify" in result["outputs"] else result["target"]
        )
        dsl_to_check = (
            result["outputs"].get("dify") or result["dsl"]
        )
        report = await validator.validate(dsl_to_check, target_for_validation)
        assert report.ok is True or all(
            i.severity != "error" for i in report.issues
        )
        assert len(result["dag"].nodes) == 2
        assert "atom.db.postgres.v1" in {n.asset_id for n in result["dag"].nodes}
