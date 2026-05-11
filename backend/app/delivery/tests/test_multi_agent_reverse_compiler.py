"""Phase 10 W4 D3 - tests for MultiAgentReverseCompiler."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from app.core.pipelines.factory.compiler import DifyCompilerImpl
from app.core.pipelines.factory.ir import ResolvedDAG, ResolvedNode
from app.core.pipelines.factory.ir.multi_agent import (
    GuardrailSpec,
    IndustryTag,
    MultiAgentSpec,
    SpecialistSpec,
    TriageSpec,
)
from app.delivery.multi_agent_reverse_compiler import (
    AgentReverseDiff,
    MultiAgentReverseCompiler,
    MultiAgentSystemDiff,
)


def _yaml_for(node_specs: list[tuple[str, str]]) -> str:
    dag = ResolvedDAG(
        intent_ref="t",
        nodes=[
            ResolvedNode(id=nid, asset_id=aid, asset_version="1.0.0",
                         confidence=0.9, selection_reason="test")
            for nid, aid in node_specs
        ],
        edges=[], target="dify",
    )
    return DifyCompilerImpl().compile(dag)


def _make_spec(*specialist_kwargs) -> MultiAgentSpec:
    return MultiAgentSpec(
        name="test_system",
        description="this is a multi-agent test system",
        industry=IndustryTag(code="01", primary="通用"),
        triage=TriageSpec(name="Triage", system_prompt_id="prompt.triage.v1",
                          initial_handoff_targets=[]),
        specialists=[
            SpecialistSpec(
                id=s["id"],
                name=s.get("name", s["id"]),
                agent_class=s.get("agent_class", "customer_service"),
                description=s.get("description", "specialist description"),
                nl_brief=s.get("nl_brief", "specialist nl brief"),
                handoff_targets=s.get("handoff_targets", []),
                tools=s.get("tools", []),
            )
            for s in specialist_kwargs
        ],
        guardrails=[GuardrailSpec(kind="relevance", description="block off-topic")],
    )


def _mapping_file(tmp_path: Path, *, slug: str,
                  triage_app_id: str | None,
                  specialists: dict[str, str]) -> Path:
    p = tmp_path / f"{slug}.multi-agent.json"
    p.write_text(json.dumps({
        "system_slug": slug,
        "updated_at": "2026-05-09T00:00:00",
        "triage_dify_app_id": triage_app_id,
        "specialists": specialists,
        "handoff_edges": [],
    }, ensure_ascii=False), encoding="utf-8")
    return p


def _fake_publisher_with_exports(exports: dict[str, str]):
    """exports = {app_id: yaml_text}"""
    pub = MagicMock()
    pub.base_url = "http://localhost:8080"
    pub._wait_console_ready = MagicMock()
    pub._ensure_admin = MagicMock()
    pub._ensure_logged_in = MagicMock()
    pub._csrf_headers = MagicMock(return_value={})

    def _get(url: str, headers=None, timeout=None):
        for app_id, ytext in exports.items():
            if f"/apps/{app_id}/export" in url:
                return 200, {"data": ytext}
        return 404, {"error": "not found"}

    pub._get_json = _get
    return pub


def test_no_change_when_yaml_matches_baseline(tmp_path: Path):
    spec = _make_spec({"id": "booking", "tools": ["atom.db.postgres.v1"]})
    yaml_text = _yaml_for([("s1", "atom.db.postgres.v1")])
    pub = _fake_publisher_with_exports({"bid": yaml_text})
    mapping = _mapping_file(tmp_path, slug="sys", triage_app_id=None,
                            specialists={"booking": "bid"})

    differ = MultiAgentReverseCompiler(pub)
    result = differ.diff_system(spec, "sys", mapping)

    by_id = {d.agent_id: d for d in result.per_agent}
    assert by_id["booking"].human_tuned is False
    assert by_id["booking"].added == []
    assert by_id["booking"].removed == []
    assert result.system_human_tuned is False


def test_human_added_atom_detected(tmp_path: Path):
    spec = _make_spec({"id": "booking", "tools": ["atom.db.postgres.v1"]})
    edited_yaml = _yaml_for([
        ("s1", "atom.db.postgres.v1"),
        ("s2", "atom.llm.chat.v1"),  # human added in Dify
    ])
    pub = _fake_publisher_with_exports({"bid": edited_yaml})
    mapping = _mapping_file(tmp_path, slug="sys", triage_app_id=None,
                            specialists={"booking": "bid"})

    differ = MultiAgentReverseCompiler(pub)
    result = differ.diff_system(spec, "sys", mapping)
    diff = result.per_agent[0]
    assert "atom.llm.chat.v1" in diff.added
    assert diff.human_tuned is True


def test_human_removed_atom_detected(tmp_path: Path):
    spec = _make_spec({"id": "booking",
                       "tools": ["atom.db.postgres.v1", "atom.llm.chat.v1"]})
    edited_yaml = _yaml_for([("s1", "atom.db.postgres.v1")])  # llm removed
    pub = _fake_publisher_with_exports({"bid": edited_yaml})
    mapping = _mapping_file(tmp_path, slug="sys", triage_app_id=None,
                            specialists={"booking": "bid"})

    differ = MultiAgentReverseCompiler(pub)
    result = differ.diff_system(spec, "sys", mapping)
    diff = result.per_agent[0]
    assert "atom.llm.chat.v1" in diff.removed
    assert diff.human_tuned is True


def test_multi_specialist_aggregate(tmp_path: Path):
    spec = _make_spec(
        {"id": "booking", "tools": ["atom.db.postgres.v1"]},
        {"id": "seat", "tools": ["atom.llm.chat.v1"]},
    )
    untouched = _yaml_for([("s1", "atom.db.postgres.v1")])
    edited = _yaml_for([("s1", "atom.llm.chat.v1"),
                        ("s2", "atom.notify.dingtalk.v1")])
    pub = _fake_publisher_with_exports({"bid": untouched, "sid": edited})
    mapping = _mapping_file(tmp_path, slug="sys", triage_app_id=None,
                            specialists={"booking": "bid", "seat": "sid"})

    result = MultiAgentReverseCompiler(pub).diff_system(spec, "sys", mapping)
    by_id = {d.agent_id: d for d in result.per_agent}
    assert by_id["booking"].human_tuned is False
    assert by_id["seat"].human_tuned is True
    assert "atom.notify.dingtalk.v1" in by_id["seat"].added
    assert result.tuned_agents == ["seat"]
    assert result.system_human_tuned is True


def test_triage_included_when_app_id_present(tmp_path: Path):
    spec = _make_spec({"id": "x", "tools": []})
    pub = _fake_publisher_with_exports({
        "tid": _yaml_for([("s1", "atom.llm.chat.v1")]),
        "xid": _yaml_for([]),
    })
    mapping = _mapping_file(tmp_path, slug="sys", triage_app_id="tid",
                            specialists={"x": "xid"})
    result = MultiAgentReverseCompiler(pub).diff_system(spec, "sys", mapping)
    agent_ids = [d.agent_id for d in result.per_agent]
    assert "triage" in agent_ids
    triage_diff = next(d for d in result.per_agent if d.agent_id == "triage")
    # baseline is empty list (triage has no declarative tools), so any
    # atom in edited yaml is an addition
    assert "atom.llm.chat.v1" in triage_diff.added


def test_missing_app_id_recorded_error(tmp_path: Path):
    spec = _make_spec({"id": "booking", "tools": ["atom.db.postgres.v1"]})
    pub = _fake_publisher_with_exports({})
    mapping = _mapping_file(tmp_path, slug="sys", triage_app_id=None,
                            specialists={"booking": ""})
    result = MultiAgentReverseCompiler(pub).diff_system(spec, "sys", mapping)
    assert result.per_agent[0].fetch_error
    assert "missing dify_app_id" in result.per_agent[0].fetch_error


def test_fetch_404_recorded_error(tmp_path: Path):
    spec = _make_spec({"id": "x", "tools": []})
    pub = _fake_publisher_with_exports({})  # no apps registered → 404
    mapping = _mapping_file(tmp_path, slug="sys", triage_app_id=None,
                            specialists={"x": "ghost-app"})
    result = MultiAgentReverseCompiler(pub).diff_system(spec, "sys", mapping)
    diff = result.per_agent[0]
    assert "HTTP 404" in diff.fetch_error


def test_missing_mapping_returns_empty(tmp_path: Path):
    spec = _make_spec({"id": "x", "tools": []})
    pub = _fake_publisher_with_exports({})
    bogus = tmp_path / "missing.json"
    result = MultiAgentReverseCompiler(pub).diff_system(spec, "sys", bogus)
    assert result.per_agent == []
    assert result.system_human_tuned is False


def test_short_summary_format(tmp_path: Path):
    spec = _make_spec({"id": "x", "tools": []})
    pub = _fake_publisher_with_exports({"xid": _yaml_for([])})
    mapping = _mapping_file(tmp_path, slug="sys", triage_app_id=None,
                            specialists={"x": "xid"})
    result = MultiAgentReverseCompiler(pub).diff_system(spec, "sys", mapping)
    summary = result.short_summary()
    assert "system=sys" in summary
    assert "agents=" in summary


def test_fetch_failures_list_populated(tmp_path: Path):
    spec = _make_spec({"id": "x", "tools": []}, {"id": "y", "tools": []})
    pub = _fake_publisher_with_exports({"xid": _yaml_for([])})  # only x ok
    mapping = _mapping_file(tmp_path, slug="sys", triage_app_id=None,
                            specialists={"x": "xid", "y": "yghost"})
    result = MultiAgentReverseCompiler(pub).diff_system(spec, "sys", mapping)
    assert "y" in result.fetch_failures
    assert "x" not in result.fetch_failures
