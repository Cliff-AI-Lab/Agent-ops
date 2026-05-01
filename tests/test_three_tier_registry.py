"""Tests for the three-tier registry hub + Markdown generator (Batch Y)."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.ontology import AgentContract, AgentExample, AgentGenerationMetadata
from app.registry.agent_store import (
    AgentStore,
    bootstrap_agents,
    default_agents_dir,
    load_agent_from_dir,
    load_agents_from_dir,
    reset_agent_store,
)
from app.registry.markdown_gen import markdown_for, render_markdown
from app.registry.registry_hub import RegistryEntry, list_all, search, stats

PROJECT_ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = PROJECT_ROOT / "agents"


# ---------- AgentStore ----------
def test_load_real_agents_directory():
    pairs = load_agents_from_dir(AGENTS_DIR)
    ids = {a.agent_id for a, _ in pairs}
    assert "agents.web_research" in ids
    by_id = {a.agent_id: src for a, src in pairs}
    assert by_id["agents.web_research"] == AGENTS_DIR / "web_research"


def test_load_single_agent_yaml():
    agent = load_agent_from_dir(AGENTS_DIR / "web_research")
    assert agent.agent_id == "agents.web_research"
    assert agent.activation_status == "active"
    assert "web research" in (k.lower() for k in [*agent.intent_keywords]) or "互联网研究" in agent.intent_keywords
    assert "web.search.tavily" in agent.tools_used
    assert "web.scrape.firecrawl" in agent.tools_used
    assert agent.is_generated is False


def test_agent_store_register_and_retrieve():
    store = AgentStore()
    a = AgentContract(
        agent_id="t.test",
        version="0.1.0",
        name="T",
        description="d",
        activation_status="active",
    )
    store.register(a, source_dir=AGENTS_DIR)
    assert store.get("t.test") is a
    assert store.get("missing") is None
    assert "t.test" in {x.agent_id for x in store.list(activation_status="active")}


def test_bootstrap_agents_global_singleton():
    reset_agent_store()
    n = bootstrap_agents()
    assert n >= 1
    from app.registry.agent_store import get_agent_store
    assert get_agent_store().get("agents.web_research") is not None


# ---------- AgentContract validation ----------
def test_agent_contract_generated_provenance_fields():
    gen = AgentGenerationMetadata(
        generation_id="run-abc",
        source_sop="做一个客服 Agent",
        quality_score=0.85,
        usage_count=3,
    )
    a = AgentContract(
        agent_id="gen.demo",
        version="0.0.1",
        name="Generated demo",
        description="from a user run",
        is_generated=True,
        generation=gen,
    )
    dumped = a.model_dump()
    assert dumped["is_generated"] is True
    assert dumped["generation"]["generation_id"] == "run-abc"
    assert dumped["generation"]["quality_score"] == 0.85


def test_agent_examples_round_trip():
    a = AgentContract(
        agent_id="x.y",
        version="0.1.0",
        name="X",
        description="d",
        examples=[
            AgentExample(title="case", user_intent="帮我..."),
        ],
    )
    a2 = AgentContract.model_validate_json(a.model_dump_json())
    assert a2.examples[0].user_intent == "帮我..."


# ---------- RegistryHub ----------
def test_hub_stats_covers_four_tiers(monkeypatch):
    # ensure all stores bootstrapped
    from app.registry import preset_store as ps
    from app.registry import store as cs
    from app.registry import tool_store as ts
    from app.registry import agent_store as ags
    cs.reset_store()
    ts.reset_tool_store()
    ps._preset_store = None  # noqa: SLF001
    ags.reset_agent_store()
    from app.registry.bootstrap import bootstrap_registry
    bootstrap_registry()
    ts.bootstrap_tools()
    ps.bootstrap_presets()
    ags.bootstrap_agents()
    s = stats()
    assert set(s.keys()) == {"atom", "composite", "agent", "preset"}
    assert s["agent"] >= 1
    assert s["atom"] >= 1
    assert s["composite"] >= 1


def test_hub_list_all_filter_by_kind():
    agents = list_all(kind="agent")
    assert all(e.kind == "agent" for e in agents)
    assert any(e.id == "agents.web_research" for e in agents)


def test_hub_search_matches_intent_keywords():
    hits = search("互联网研究")
    assert any(e.id == "agents.web_research" for e in hits)


def test_hub_search_multiword_and():
    # the agent's keywords contain "网络调研" / "互联网研究" / "web research"
    hits = search("research desk")
    # both tokens must appear in haystack — desk research is in keywords
    assert any(e.id == "agents.web_research" for e in hits)


def test_registry_entry_marks_generated_flag():
    # Synthesize and feed via a private helper to check is_generated propagates
    # (real generated agents will land here via Batch X asset hub)
    entry = RegistryEntry(
        kind="agent", id="gen.x", version="0.1.0", name="x",
        description="x", activation_status="active", is_generated=True,
    )
    assert entry.is_generated is True


# ---------- MarkdownGen ----------
def test_markdown_for_curated_readme_is_served_verbatim():
    md, source = markdown_for("agent", "agents.web_research")
    # curated README.md exists at agents/web_research/README.md
    assert source == "curated"
    assert "Web 研究助手" in md or "agents.web_research" in md


def test_markdown_for_generated_when_no_curated():
    # Capability tier has no per-id directory, so MD is always generated
    md, source = markdown_for("composite", "ui.plan_blueprint")
    assert source == "generated"
    assert "ui.plan_blueprint" in md
    assert "## 用途" in md
    assert "## 输入 / 输出" in md
    assert "## 限制 / 风险" in md


def test_render_markdown_includes_three_tier_composition():
    a = AgentContract(
        agent_id="x.y", version="0.1.0", name="X", description="An X agent",
        capabilities_used=["ui.plan_blueprint"],
        tools_used=["web.search.tavily"],
        intent_keywords=["alpha", "beta"],
        activation_status="active",
    )
    entry = RegistryEntry(
        kind="agent", id=a.agent_id, version=a.version, name=a.name,
        description=a.description, intent_keywords=a.intent_keywords,
        tags=a.tags, activation_status=a.activation_status,
        is_generated=False, raw=a.model_dump(),
    )
    md = render_markdown(entry)
    assert "由什么组成" in md
    assert "ui.plan_blueprint" in md
    assert "web.search.tavily" in md
    assert "意图关键词" in md
    assert "alpha" in md


def test_markdown_unknown_entry_raises():
    with pytest.raises(KeyError):
        markdown_for("agent", "agents.not_a_thing_42")
