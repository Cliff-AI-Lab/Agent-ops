"""Tests for Tool Wiki (Batch C+): ToolContract + ToolStore + loader."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.ontology import ToolContract
from app.registry import (
    ToolStore,
    bootstrap_tools,
    default_tools_dir,
    load_tool_from_yaml,
    load_tools_from_dir,
    reset_tool_store,
)
from app.registry.tool_store import get_tool_store


# ---------- ToolContract schema ----------

def test_tool_contract_minimum_fields() -> None:
    t = ToolContract(
        tool_id="demo.ocr.cheap",
        name="Demo OCR",
        description="…",
        provider="Demo Inc",
        category="ocr",
    )
    assert t.version == "0.1.0"
    assert t.activation_status == "draft"


def test_tool_contract_rejects_unknown_category() -> None:
    with pytest.raises(ValueError):
        ToolContract(
            tool_id="x",
            name="x",
            description="x",
            provider="x",
            category="not-a-category",
        )


# ---------- YAML loader ----------

def test_load_single_tool_yaml(tmp_path: Path) -> None:
    yaml_text = """
tool_id: test.ocr.mock
version: 0.1.0
name: Test OCR
description: Test.
provider: Test
category: ocr
tags: [test]
transport_type: http
auth_mode: api_key
auth_env_vars: [TEST_KEY]
pricing_model: free
activation_status: active
"""
    p = tmp_path / "x.yaml"
    p.write_text(yaml_text, encoding="utf-8")
    t = load_tool_from_yaml(p)
    assert t.tool_id == "test.ocr.mock"
    assert t.category == "ocr"


def test_load_real_tools_dir_recursive_7_items() -> None:
    tools = load_tools_from_dir(default_tools_dir())
    ids = sorted(t.tool_id for t in tools)
    assert len(ids) == 7
    assert "baidu.ocr.general_accurate" in ids
    assert "baidu.speech.tts" in ids
    assert "baidu.speech.asr" in ids
    assert "web.scrape.firecrawl" in ids
    assert "web.search.tavily" in ids
    assert "analysis.report.docanalyze" in ids
    assert "openai.image.dalle3" in ids


# ---------- ToolStore ----------

def _mk(tool_id: str, *, provider: str = "Test", category: str = "ocr",
        status: str = "active", tags: list[str] | None = None) -> ToolContract:
    return ToolContract(
        tool_id=tool_id, name=tool_id, description="...",
        provider=provider, category=category,
        tags=tags or [], activation_status=status,
    )


def test_tool_store_list_filters() -> None:
    s = ToolStore()
    s.register(_mk("a", category="ocr", provider="Baidu AI"))
    s.register(_mk("b", category="speech_tts", provider="Baidu AI"))
    s.register(_mk("c", category="ocr", provider="Internal", status="draft"))

    assert len(s.list()) == 3
    assert len(s.list(activation_status="active")) == 2
    assert [t.tool_id for t in s.list(category="ocr")] == ["a", "c"]
    assert [t.tool_id for t in s.list(provider="Baidu AI")] == ["a", "b"]


def test_tool_store_search() -> None:
    s = ToolStore()
    s.register(_mk("baidu.ocr.v1", tags=["chinese"]))
    s.register(_mk("tavily.search", category="web_search", tags=["llm"]))
    s.register(_mk("baidu.tts", category="speech_tts"))

    hits = s.search("baidu")
    assert {t.tool_id for t in hits} == {"baidu.ocr.v1", "baidu.tts"}

    hits2 = s.search("CHINESE")
    assert [t.tool_id for t in hits2] == ["baidu.ocr.v1"]


def test_tool_store_categories_and_providers_counts() -> None:
    s = ToolStore()
    s.register(_mk("a", category="ocr", provider="A"))
    s.register(_mk("b", category="ocr", provider="B"))
    s.register(_mk("c", category="speech_tts", provider="A"))
    assert s.categories() == {"ocr": 2, "speech_tts": 1}
    assert s.providers() == {"A": 2, "B": 1}


def test_bootstrap_tools_loads_7_from_project_dir() -> None:
    reset_tool_store()
    n = bootstrap_tools()
    assert n == 7
    store = get_tool_store()
    assert len(store.list("active")) == 7
    cats = store.categories()
    assert cats.get("ocr") == 1
    assert cats.get("speech_tts") == 1
    assert cats.get("web_scrape") == 1
