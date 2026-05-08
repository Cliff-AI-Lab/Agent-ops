"""Tests for Phase 8 Day 2: atom-level Jinja2 template projection.

The compiler must:
1. Prefer atom.projections.dify.template (Jinja2 -> YAML) when present.
2. Fall back to built-in _NODE_TYPE_MAP when template missing/legacy/broken.
3. Yield Dify-import-compatible YAML in either path.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.core.pipelines.factory.compiler import DifyCompilerImpl
from app.core.pipelines.factory.compiler.impl import _HELPERS
from app.core.pipelines.factory.ir import ResolvedDAG, ResolvedNode
from app.registry.atom_loader import AtomLoaderImpl


@pytest.fixture(scope="module")
def loaded_atoms():
    repo_root = Path(__file__).resolve().parents[7]
    base = repo_root / "capabilities" / "atom"
    if not base.exists():
        pytest.skip(f"no atom dir: {base}")
    return AtomLoaderImpl().load_all(base)


def _node(nid: str, asset_id: str, **kwargs) -> ResolvedNode:
    return ResolvedNode(
        id=nid,
        asset_id=asset_id,
        asset_version="1.0.0",
        confidence=0.9,
        selection_reason="atom-template test",
        **kwargs,
    )


def _compile(atoms, nodes, edges=()) -> dict:
    c = DifyCompilerImpl()
    c.index(atoms)
    dag = ResolvedDAG(
        intent_ref="atom-tmpl-test",
        nodes=list(nodes),
        edges=list(edges),
        target="dify",
    )
    return yaml.safe_load(c.compile(dag))


def test_helpers_ruidong_placeholder():
    assert _HELPERS.ruidong_model_placeholder("通用对话", "中") == "${RUIDONG_MODEL_FOR_通用对话_中}"
    assert _HELPERS.ruidong_model_placeholder("结构化抽取", "大") == "${RUIDONG_MODEL_FOR_结构化抽取_大}"
    # task type with embedded space + slash gets normalized
    # "Agent / Tool calling" -> spaces become "_", slash stripped:
    # "Agent_/_Tool_calling" -> "Agent__Tool_calling"
    assert _HELPERS.ruidong_model_placeholder("Agent / Tool calling", "大") == "${RUIDONG_MODEL_FOR_Agent__Tool_calling_大}"


def test_llm_atom_template_emits_dify_llm_node(loaded_atoms):
    """LLM atom template must render a node with all Dify llm-node fields."""
    parsed = _compile(
        loaded_atoms,
        [_node("s1", "atom.llm.chat.v1", llm_task_type="通用对话", llm_size="中",
               llm_fallback_chain=["中", "大"], prompt_id="prompt.daily_report.v1")],
    )
    llm = next(n for n in parsed["workflow"]["graph"]["nodes"] if n["data"]["type"] == "llm")
    d = llm["data"]
    # template-driven shape
    assert d["model"]["provider"] == "langgenius/openai_api_compatible/openai_api_compatible"
    assert d["model"]["mode"] == "chat"
    assert "RUIDONG_MODEL_FOR_通用对话_中" in d["model"]["name"]
    assert d["model"]["completion_params"]["temperature"] == 0.7
    assert d["context"] == {"enabled": False, "variable_selector": []}
    assert d["vision"] == {"enabled": False}
    assert isinstance(d["prompt_template"], list) and len(d["prompt_template"]) >= 1
    # system message carries prompt_id reference (not inline prompt text)
    sys_msg = d["prompt_template"][0]
    assert sys_msg["role"] == "system"
    assert "prompt.daily_report.v1" in sys_msg["text"]
    # _llm_routing carries fallback metadata
    assert d["_llm_routing"]["task_type"] == "通用对话"
    assert d["_llm_routing"]["fallback_chain"] == ["中", "大"]
    assert d["_llm_routing"]["prompt_id"] == "prompt.daily_report.v1"


def test_db_atom_template_emits_dify_code_node(loaded_atoms):
    """DB postgres atom template must render a Dify code node with psycopg2."""
    parsed = _compile(loaded_atoms, [_node("s1", "atom.db.postgres.v1")])
    code = next(n for n in parsed["workflow"]["graph"]["nodes"] if n["id"] == "s1")
    d = code["data"]
    assert d["type"] == "code"
    assert d["code_language"] == "python3"
    assert "psycopg2" in d["code"]
    assert "PG_HOST" in d["code"]  # connection params via env, not hardcoded
    assert d["outputs"]["rows"]["type"] == "array[object]"


def test_http_atom_template_emits_dify_http_request_node(loaded_atoms):
    """HTTP generic atom template must render a Dify http-request node."""
    parsed = _compile(loaded_atoms, [_node("s1", "atom.http.generic.v1")])
    http = next(n for n in parsed["workflow"]["graph"]["nodes"] if n["id"] == "s1")
    d = http["data"]
    assert d["type"] == "http-request"
    assert d["method"] == "POST"
    assert d["url"] == "${PLACEHOLDER_URL}"
    assert d["body"]["type"] == "json"
    assert d["authorization"]["type"] == "no-auth"


def test_dingtalk_atom_template_emits_markdown_webhook(loaded_atoms):
    """DingTalk notify atom must render a markdown webhook POST."""
    parsed = _compile(loaded_atoms, [_node("s1", "atom.notify.dingtalk.v1")])
    notify = next(n for n in parsed["workflow"]["graph"]["nodes"] if n["id"] == "s1")
    d = notify["data"]
    assert d["type"] == "http-request"
    assert d["method"] == "POST"
    assert d["url"] == "${DINGTALK_WEBHOOK_URL}"
    msgtypes = [item for item in d["body"]["data"] if item["key"] == "msgtype"]
    assert msgtypes and msgtypes[0]["value"] == "markdown"


def test_schedule_atom_falls_back_to_builtin_code_node(loaded_atoms):
    """Schedule atom is not_supported in Dify; compiler must fall back."""
    parsed = _compile(loaded_atoms, [_node("s1", "atom.schedule.cron.v1")])
    sched = next(n for n in parsed["workflow"]["graph"]["nodes"] if n["id"] == "s1")
    # built-in _code_data path leaves a placeholder Python body
    assert sched["data"]["type"] == "code"
    assert "placeholder for atom" in sched["data"]["code"]


def test_atom_template_path_writes_factory_metadata(loaded_atoms):
    """Even when template is used, _factory metadata must still be attached."""
    parsed = _compile(loaded_atoms, [_node("s1", "atom.llm.chat.v1",
                                          llm_task_type="通用对话", llm_size="中")])
    llm = next(n for n in parsed["workflow"]["graph"]["nodes"] if n["data"]["type"] == "llm")
    assert llm["data"]["_factory"]["asset_id"] == "atom.llm.chat.v1"
    assert llm["data"]["_factory"]["asset_version"] == "1.0.0"
    assert llm["data"]["_factory"]["confidence"] == 0.9


def test_unknown_atom_falls_back_gracefully():
    """When atom is unknown to the loader, compiler must not crash."""
    parsed = _compile(
        atoms={},  # empty atom index
        nodes=[_node("s1", "atom.db.postgres.v1")],
    )
    sched = next(n for n in parsed["workflow"]["graph"]["nodes"] if n["id"] == "s1")
    # falls back to built-in code path with subcategory=Unknown
    assert sched["data"]["type"] == "code"
