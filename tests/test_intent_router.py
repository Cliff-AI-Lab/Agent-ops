"""IntentRouter tests (Batch Z)."""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.core.llm.client import ChatMessage
from app.core.orchestrator.intent_router import (
    IntentBreakdown,
    IntentRouter,
    IntentSubTask,
    _validate_spec_refs,
)
from app.ontology import WorkflowSpec, WorkflowStep
from app.registry.registry_hub import RegistryEntry


# Bootstrap real registry once per module so hub_search returns real entries
@pytest.fixture(scope="module", autouse=True)
def _bootstrap_registry():
    from app.registry import agent_store as ags
    from app.registry import store as cs
    from app.registry import tool_store as ts
    from app.registry.bootstrap import bootstrap_registry

    cs.reset_store()
    ts.reset_tool_store()
    ags.reset_agent_store()
    bootstrap_registry()
    ts.bootstrap_tools()
    ags.bootstrap_agents()
    yield


# ---------- Fake LLM (round-robin canned responses) ----------
class FakeLLM:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def chat(
        self,
        model: str,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, object]] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ChatMessage:
        self.calls.append({"model": model, "messages": messages})
        if not self._responses:
            raise RuntimeError("FakeLLM ran out of canned responses")
        return ChatMessage(role="assistant", content=self._responses.pop(0))


# ---------- Test fixtures ----------
@pytest.fixture
def sample_breakdown() -> IntentBreakdown:
    return IntentBreakdown(
        intent_summary="对一篇 PDF 报告做总结",
        sub_tasks=[
            IntentSubTask(task="OCR 提取 PDF 文本", keywords=["OCR", "扫描", "PDF"]),
            IntentSubTask(task="对文本生成摘要", keywords=["报告摘要", "总结"]),
        ],
    )


@pytest.fixture
def sample_shortlist() -> list[RegistryEntry]:
    return [
        RegistryEntry(
            kind="atom", id="baidu.ocr.general_accurate", version="0.1.0",
            name="Baidu OCR", description="OCR text from images.",
            intent_keywords=["OCR", "识别"], tags=["ocr"],
            activation_status="active", is_generated=False,
            raw={"input_schema": {"properties": {"image": {}}}},
        ),
        RegistryEntry(
            kind="atom", id="analysis.report.docanalyze", version="0.1.0",
            name="Doc Analyzer", description="Summarize and extract key points.",
            intent_keywords=["报告", "摘要"], tags=["analysis"],
            activation_status="active", is_generated=False,
            raw={"input_schema": {"properties": {"text": {}}}},
        ),
    ]


# ---------- _validate_spec_refs ----------
def test_validate_spec_refs_clean(sample_shortlist):
    spec = WorkflowSpec(
        workflow_id="wf.demo",
        version="0.1.0",
        steps=[
            WorkflowStep(id="s1", capability="baidu.ocr.general_accurate"),
            WorkflowStep(id="s2", capability="analysis.report.docanalyze", depends_on=["s1"]),
        ],
    )
    errs = _validate_spec_refs(spec, sample_shortlist)
    assert errs == []


def test_validate_spec_refs_unknown_capability(sample_shortlist):
    spec = WorkflowSpec(
        workflow_id="wf.bad", version="0.1.0",
        steps=[WorkflowStep(id="s1", capability="foo.bar.invented")],
    )
    errs = _validate_spec_refs(spec, sample_shortlist)
    assert any("foo.bar.invented" in e for e in errs)


def test_validate_spec_refs_dangling_depends_on(sample_shortlist):
    spec = WorkflowSpec(
        workflow_id="wf.dangle", version="0.1.0",
        steps=[
            WorkflowStep(id="s1", capability="baidu.ocr.general_accurate", depends_on=["s99"]),
        ],
    )
    errs = _validate_spec_refs(spec, sample_shortlist)
    assert any("s99" in e and "depends_on" in e for e in errs)


def test_validate_spec_refs_duplicate_step_ids(sample_shortlist):
    spec = WorkflowSpec(
        workflow_id="wf.dup", version="0.1.0",
        steps=[
            WorkflowStep(id="s1", capability="baidu.ocr.general_accurate"),
            WorkflowStep(id="s1", capability="analysis.report.docanalyze"),
        ],
    )
    errs = _validate_spec_refs(spec, sample_shortlist)
    assert any("duplicate step id" in e for e in errs)


# ---------- IntentRouter end-to-end (mocked LLM + real RegistryHub) ----------
@pytest.mark.asyncio
async def test_intent_router_full_pipeline_clean_path():
    """Mock LLM returns a valid IntentBreakdown then a valid WorkflowSpec; spec_valid=True."""
    breakdown = {
        "intent_summary": "做互联网调研",
        "sub_tasks": [
            {"task": "搜索网页", "keywords": ["互联网研究", "调研", "搜索"]},
            {"task": "抓取页面内容", "keywords": ["scrape", "抓取"]},
        ],
    }
    spec = {
        "workflow_id": "wf.research",
        "version": "0.1.0",
        "name": "网络调研流程",
        "trigger_type": "manual",
        "steps": [
            {"id": "s1", "capability": "web.search.tavily", "depends_on": [],
             "input_mapping": {"query": "inputs.topic"}, "on_failure": "fail",
             "max_retries": 0, "approval_required": False},
            {"id": "s2", "capability": "web.scrape.firecrawl", "depends_on": ["s1"],
             "input_mapping": {"url": "steps.s1.url"}, "on_failure": "fail",
             "max_retries": 0, "approval_required": False},
        ],
    }
    fake = FakeLLM([json.dumps(breakdown), json.dumps(spec)])
    router = IntentRouter(fake, light_model="haiku", heavy_model="sonnet", shortlist_top_k=4)
    result = await router.plan("帮我做一份关于 LLM 框架的调研")

    assert result.intent_summary == "做互联网调研"
    assert len(result.sub_tasks) == 2
    assert len(result.candidate_shortlist) >= 1
    assert any(c.id == "web.search.tavily" for c in result.candidate_shortlist)
    assert result.workflow_spec.workflow_id == "wf.research"
    assert len(result.workflow_spec.steps) == 2
    assert result.spec_valid is True
    assert result.validation_errors == []
    assert result.fallback_used is False
    assert len(fake.calls) == 2


@pytest.mark.asyncio
async def test_intent_router_invalid_spec_caught_by_validator():
    """Planner emits a spec referencing an invented capability — spec_valid=False."""
    breakdown = {
        "intent_summary": "demo",
        "sub_tasks": [{"task": "do something", "keywords": ["test"]}],
    }
    bad_spec = {
        "workflow_id": "wf.bad", "version": "0.1.0",
        "steps": [
            {"id": "s1", "capability": "totally.fake.capability", "depends_on": [],
             "input_mapping": {}, "on_failure": "fail", "max_retries": 0,
             "approval_required": False},
        ],
    }
    fake = FakeLLM([json.dumps(breakdown), json.dumps(bad_spec)])
    router = IntentRouter(fake, light_model="haiku", heavy_model="sonnet")
    result = await router.plan("test sop")
    assert result.spec_valid is False
    assert any("totally.fake.capability" in e for e in result.validation_errors)


@pytest.mark.asyncio
async def test_intent_router_repair_on_invalid_breakdown_json():
    """First LLM call returns a malformed breakdown → Repair triggers a second LLM call."""
    invalid = "{not valid json"
    valid_breakdown = {
        "intent_summary": "ok",
        "sub_tasks": [{"task": "x", "keywords": ["a"]}],
    }
    valid_spec = {
        "workflow_id": "w", "version": "0.1.0",
        "steps": [
            {"id": "s1", "capability": "web.search.tavily", "depends_on": [],
             "input_mapping": {}, "on_failure": "fail", "max_retries": 0,
             "approval_required": False},
        ],
    }
    fake = FakeLLM([invalid, json.dumps(valid_breakdown), json.dumps(valid_spec)])
    router = IntentRouter(fake, light_model="haiku", heavy_model="sonnet")
    result = await router.plan("test sop with bad first response")
    assert result.intent_summary == "ok"
    assert result.spec_valid is True
    # 1 invalid breakdown + 1 valid breakdown + 1 valid spec = 3 calls
    assert len(fake.calls) == 3


@pytest.mark.asyncio
async def test_intent_router_emits_trace_events(monkeypatch):
    """The four phases each emit one L3 trace event."""
    from app.core.orchestrator import intent_router as ir_mod

    captured: list[tuple[str, str, str, str]] = []
    real_emit = ir_mod.emit

    def spy(layer, component, kind, message, **kwargs):
        captured.append((layer, component, kind, message))
        return real_emit(layer, component, kind, message, **kwargs)

    monkeypatch.setattr(ir_mod, "emit", spy)

    breakdown = {
        "intent_summary": "x",
        "sub_tasks": [{"task": "y", "keywords": ["互联网研究"]}],
    }
    spec = {
        "workflow_id": "w", "version": "0.1.0",
        "steps": [
            {"id": "s1", "capability": "web.search.tavily", "depends_on": [],
             "input_mapping": {}, "on_failure": "fail", "max_retries": 0,
             "approval_required": False},
        ],
    }
    fake = FakeLLM([json.dumps(breakdown), json.dumps(spec)])
    router = IntentRouter(fake, light_model="haiku", heavy_model="sonnet")
    await router.plan("trace me")

    kinds = [k for (_, comp, k, _msg) in captured if comp == "IntentRouter"]
    assert "start" in kinds
    assert "intent_extracted" in kinds
    assert "candidates_found" in kinds
    assert "spec_validated" in kinds or "spec_invalid" in kinds


@pytest.mark.asyncio
async def test_intent_router_min_shortlist_padded(monkeypatch):
    """Even when sub-tasks barely match anything, shortlist >= min_shortlist."""
    from app.core.orchestrator import intent_router as ir_mod

    # Force hub_search to return an empty list so we exercise the padding branch
    monkeypatch.setattr(ir_mod, "hub_search", lambda *a, **kw: [])

    breakdown = {
        "intent_summary": "极端冷门请求",
        "sub_tasks": [{"task": "totallyunrelatedalienquery", "keywords": ["alienquery"]}],
    }
    spec = {
        "workflow_id": "w", "version": "0.1.0",
        "steps": [
            {"id": "s1", "capability": "web.search.tavily", "depends_on": [],
             "input_mapping": {}, "on_failure": "fail", "max_retries": 0,
             "approval_required": False},
        ],
    }
    fake = FakeLLM([json.dumps(breakdown), json.dumps(spec)])
    router = IntentRouter(fake, light_model="haiku", heavy_model="sonnet",
                          shortlist_top_k=2, min_shortlist=5)
    result = await router.plan("test min shortlist")
    assert len(result.candidate_shortlist) >= 5
    kinds = {c.kind for c in result.candidate_shortlist}
    # Padding pass tries to cover multiple tiers
    assert len(kinds) >= 2


@pytest.mark.asyncio
async def test_intent_router_cross_tier_boost_pulls_in_keyword_overlap(monkeypatch):
    """An agent whose intent_keywords overlap a sub-task token should be in shortlist
    even if hub_search misses it (Batch G cross-tier boost)."""
    from app.core.orchestrator import intent_router as ir_mod
    from app.registry import registry_hub as rh

    fake_agent = RegistryEntry(
        kind="agent", id="gen.fake-weekly-1", version="0.1.0",
        name="Fake Weekly Generator",
        description="Auto-generated agent that builds weekly reports.",
        intent_keywords=["周报", "weekly report", "team summary"],
        tags=["generated"],
        activation_status="active", is_generated=True,
        raw={},
    )
    real_list_all = rh.list_all
    monkeypatch.setattr(rh, "list_all",
                        lambda **kw: real_list_all(**kw) + [fake_agent])
    # hub_search misses it
    monkeypatch.setattr(ir_mod, "hub_search", lambda *a, **kw: [])

    breakdown = {
        "intent_summary": "周报",
        "sub_tasks": [{"task": "做一个周报", "keywords": ["周报"]}],
    }
    spec = {
        "workflow_id": "w", "version": "0.1.0",
        "steps": [
            {"id": "s1", "capability": "web.search.tavily", "depends_on": [],
             "input_mapping": {}, "on_failure": "fail", "max_retries": 0,
             "approval_required": False},
        ],
    }
    fake = FakeLLM([json.dumps(breakdown), json.dumps(spec)])
    router = IntentRouter(fake, light_model="haiku", heavy_model="sonnet")
    result = await router.plan("做一个周报")
    assert any(c.id == "gen.fake-weekly-1" for c in result.candidate_shortlist), \
        "cross-tier boost should pull in agents whose intent_keywords overlap"


@pytest.mark.asyncio
async def test_intent_router_shortlist_dedupes_across_subtasks():
    """If two sub-tasks both match the same registry entry, candidate_shortlist still unique."""
    breakdown = {
        "intent_summary": "x",
        "sub_tasks": [
            {"task": "搜资料", "keywords": ["互联网研究"]},
            {"task": "找网页", "keywords": ["互联网研究"]},  # same keyword
        ],
    }
    spec = {
        "workflow_id": "w", "version": "0.1.0",
        "steps": [
            {"id": "s1", "capability": "web.search.tavily", "depends_on": [],
             "input_mapping": {}, "on_failure": "fail", "max_retries": 0,
             "approval_required": False},
        ],
    }
    fake = FakeLLM([json.dumps(breakdown), json.dumps(spec)])
    router = IntentRouter(fake, light_model="haiku", heavy_model="sonnet", shortlist_top_k=4)
    result = await router.plan("test")
    ids = [c.id for c in result.candidate_shortlist]
    assert len(ids) == len(set(ids)), "shortlist must have unique ids"
