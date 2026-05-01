"""End-to-end test: NL -> Intent -> ResolvedDAG -> Dify YAML.

Real atoms loaded from capabilities/atom/. Mocked LLM (both IntentParser
and Resolver Rank). Full pipeline wired.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from app.core.llm.client import ChatMessage
from app.core.pipelines.factory.pipeline import FactoryPipeline


REPO_ROOT = Path(__file__).resolve().parents[1]
ATOMS_DIR = REPO_ROOT / "capabilities" / "atom"


def _mock_router(model: str = "test-model"):
    r = MagicMock()
    r.resolve = MagicMock(return_value=model)
    return r


@pytest.mark.asyncio
async def test_e2e_sales_weekly_report():
    """Full pipeline: NL -> Dify YAML for the MVP scenario."""
    if not ATOMS_DIR.exists():
        pytest.skip(f"atoms dir missing: {ATOMS_DIR}")

    intent_payload = {
        "schema_version": "1.0",
        "goal": "每周一上午9点 销售周报推钉钉",
        "trigger": {"type": "cron", "cron_expr": "0 9 * * 1", "natural_language": "每周一上午9点"},
        "steps": [
            {
                "id": "s1",
                "verb": "cron 定时触发",
                "expected_output_kind": "void",
                "suggested_subcategory": "Schedule",
            },
            {
                "id": "s2",
                "verb": "查询销售数据库",
                "expected_output_kind": "tabular_data",
                "suggested_subcategory": "DB",
                "inputs": {"trigger": "$s1.output"},
            },
            {
                "id": "s3",
                "verb": "撰写中文周报",
                "expected_output_kind": "natural_language",
                "suggested_subcategory": "LLM",
                "inputs": {"data": "$s2.output"},
            },
            {
                "id": "s4",
                "verb": "推送到钉钉",
                "expected_output_kind": "void",
                "suggested_subcategory": "Notify",
                "inputs": {"content": "$s3.output"},
            },
        ],
        "outputs": [{"name": "status", "type": "void", "sink": "dingtalk:运营群"}],
        "constraints": {"language": "zh", "privacy": "internal"},
        "industry": {"code": "01", "primary": "通用", "sub": "商业运营"},
        "raw_user_input": "每周一上午9点 销售周报推钉钉",
    }

    rank_payload = {
        "asset_id": "atom.llm.chat.v1",
        "confidence": 0.9,
        "reason": "mocked",
        "llm_task_type": "长文档分块总结",
        "llm_size": "中",
    }

    llm = MagicMock()
    llm.chat = AsyncMock(
        side_effect=[
            ChatMessage(role="assistant", content=json.dumps(intent_payload)),
            ChatMessage(role="assistant", content=json.dumps(rank_payload)),
            ChatMessage(role="assistant", content=json.dumps(rank_payload)),
            ChatMessage(role="assistant", content=json.dumps(rank_payload)),
            ChatMessage(role="assistant", content=json.dumps(rank_payload)),
        ]
    )

    pipeline = FactoryPipeline(
        atoms_dir=ATOMS_DIR,
        llm_client=llm,
        model_router=_mock_router(),
    )

    result = await pipeline.build("每周一上午9点 销售周报推钉钉")

    assert result["target"] == "hybrid"
    assert len(result["dag"].nodes) == 4

    asset_ids = {n.asset_id for n in result["dag"].nodes}
    assert "atom.schedule.cron.v1" in asset_ids
    assert "atom.db.postgres.v1" in asset_ids
    assert "atom.llm.chat.v1" in asset_ids
    assert "atom.notify.dingtalk.v1" in asset_ids

    # Hybrid emits BOTH outputs in result['outputs'] dict
    assert "outputs" in result
    assert "dify" in result["outputs"]
    assert "n8n" in result["outputs"]

    # Dify side: only LLM node (s3)
    parsed = yaml.safe_load(result["outputs"]["dify"])
    assert "app" in parsed
    assert "workflow" in parsed
    assert "factory_metadata" in parsed["workflow"]
    dify_nodes = parsed["workflow"]["graph"]["nodes"]
    assert {n["id"] for n in dify_nodes} == {"s3"}
    assert dify_nodes[0]["type"] == "llm"
    assert dify_nodes[0]["data"]["llm_routing"]["task_type"] == "长文档分块总结"

    # n8n side: schedule + db + notify
    import json as _json
    n8n_json = _json.loads(result["outputs"]["n8n"])
    assert "nodes" in n8n_json
    assert "connections" in n8n_json
    n8n_node_names = [n["name"] for n in n8n_json["nodes"]]
    assert any("(s1)" in n for n in n8n_node_names)
    assert any("(s2)" in n for n in n8n_node_names)
    assert any("(s4)" in n for n in n8n_node_names)
    assert not any("(s3)" in n for n in n8n_node_names)
