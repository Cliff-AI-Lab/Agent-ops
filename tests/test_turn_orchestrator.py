"""TurnOrchestrator tests (Batch I)."""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.core.dialog.session import SessionStore
from app.core.dialog.state_machine import DialogState
from app.core.dialog.turn_orchestrator import HELP_MESSAGE, TurnOrchestrator
from app.core.llm.client import ChatMessage
from app.core.orchestrator.intent_router import (
    IntentRouteResult,
    IntentBreakdown,
    IntentSubTask,
)
from app.core.orchestrator.triage import (
    TriageDecision,
    TriageRouteResult,
    TriageTarget,
)
from app.core.workflow_engine.invoker import InvocationResult
from app.ontology import WorkflowSpec, WorkflowStep


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


# ---------- Stubs ----------
class StubLLM:
    async def chat(self, *args, **kwargs):  # noqa: ANN001,ANN002,ANN003
        raise RuntimeError("StubLLM should not be called when triage/intent are stubbed")


class StubTriage:
    def __init__(self, decision: TriageDecision, candidate_ids: list[str] | None = None) -> None:
        self.decision = decision
        self.candidate_ids = candidate_ids or []
        self.calls: list[tuple[str, str | None]] = []

    async def route(self, msg: str, *, session_summary: str | None = None) -> TriageRouteResult:
        self.calls.append((msg, session_summary))
        return TriageRouteResult(
            user_message=msg,
            decision=self.decision,
            candidate_agent_ids=self.candidate_ids,
            fallback_used=False,
            attempts=1,
        )


class StubInvoker:
    def __init__(self, result: InvocationResult) -> None:
        self.result = result
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def invoke(self, capability_id, inputs, *, kind_hint=None):  # noqa: ANN001,ANN003
        self.calls.append((capability_id, dict(inputs)))
        return self.result


class StubIntentRouter:
    def __init__(self, plan_result: IntentRouteResult) -> None:
        self.plan_result = plan_result
        self.calls: list[str] = []

    async def plan(self, sop: str) -> IntentRouteResult:
        self.calls.append(sop)
        return self.plan_result


class StubReceptionist:
    """Receptionist stub that just yields the canonical 3 events and writes to the session."""

    def __init__(self, store: SessionStore) -> None:
        self.store = store
        self.calls: list[tuple[str, str]] = []

    async def turn(self, session_id: str, user_msg: str):
        self.calls.append((session_id, user_msg))
        sess = await self.store.get(session_id)
        sess.messages.append(ChatMessage(role="user", content=user_msg))
        sess.messages.append(ChatMessage(role="assistant", content="模拟接待员回复"))
        await self.store.save(sess)
        yield {"type": "state", "value": sess.state.value}
        yield {"type": "token", "value": "模拟接待员回复"}
        yield {"type": "turn_end"}


@pytest.fixture
async def store_with_session(tmp_path):
    db_path = str(tmp_path / "turn.db")
    store = SessionStore(db_path)
    sess = await store.create()
    sess.messages.append(ChatMessage(role="user", content="想做一个发票工具"))
    await store.save(sess)
    return store, sess.id


# ---------- Receptionist branch ----------
@pytest.mark.asyncio
async def test_route_receptionist_delegates_to_receptionist(store_with_session):
    store, sid = store_with_session
    triage = StubTriage(TriageDecision(
        target=TriageTarget.RECEPTIONIST, target_id=None,
        reason="新需求", forwarded_message="想做一个发票工具",
    ))
    receptionist = StubReceptionist(store)
    orch = TurnOrchestrator(
        StubLLM(), store, default_model="m",
        triage=triage, receptionist=receptionist,
    )
    events = [e async for e in orch.turn(sid, "再描述一下需求")]
    types = [e["type"] for e in events]
    assert types[0] == "triage_decision"
    assert events[0]["data"]["target"] == "receptionist"
    assert "token" in types and "turn_end" in types
    assert receptionist.calls == [(sid, "再描述一下需求")]


# ---------- Help branch ----------
@pytest.mark.asyncio
async def test_route_help_emits_templated_message(store_with_session):
    store, sid = store_with_session
    triage = StubTriage(TriageDecision(
        target=TriageTarget.HELP, target_id=None,
        reason="meta", forwarded_message="平台用法",
    ))
    orch = TurnOrchestrator(
        StubLLM(), store, default_model="m",
        triage=triage, receptionist=StubReceptionist(store),
    )
    events = [e async for e in orch.turn(sid, "Agent Ops 是啥?")]
    token_evt = next(e for e in events if e["type"] == "token")
    assert token_evt["value"] == HELP_MESSAGE
    # Help message persisted into session
    sess = await store.get(sid)
    assert sess.messages[-1].content == HELP_MESSAGE


# ---------- Generated agent branch ----------
@pytest.mark.asyncio
async def test_route_generated_agent_invokes_capability(store_with_session):
    store, sid = store_with_session
    triage = StubTriage(
        TriageDecision(
            target=TriageTarget.GENERATED_AGENT,
            target_id="gen.weekly-1",
            reason="召唤周报工具",
            forwarded_message="生成本周周报",
        ),
        candidate_ids=["gen.weekly-1"],
    )
    invoker = StubInvoker(InvocationResult(
        capability_id="gen.weekly-1", kind="agent",
        output={"weekly_report": "本周完成 12 PR、3 个里程碑"},
        used_mock=False, error=None, elapsed_ms=42,
    ))
    orch = TurnOrchestrator(
        StubLLM(), store, default_model="m",
        triage=triage, receptionist=StubReceptionist(store),
        invoker=invoker,
    )
    events = [e async for e in orch.turn(sid, "用周报工具帮我生成本周周报")]
    invoked = next(e for e in events if e["type"] == "agent_invoked")
    assert invoked["data"]["agent_id"] == "gen.weekly-1"
    assert invoked["data"]["error"] is None
    assert invoked["data"]["used_mock"] is False
    # Forwarded message reaches the invoker, NOT the raw user message
    assert invoker.calls == [("gen.weekly-1", {"message": "生成本周周报"})]
    # Final assistant message names the agent
    sess = await store.get(sid)
    assert "gen.weekly-1" in sess.messages[-1].content


@pytest.mark.asyncio
async def test_route_generated_agent_handles_invocation_error(store_with_session):
    store, sid = store_with_session
    triage = StubTriage(TriageDecision(
        target=TriageTarget.GENERATED_AGENT,
        target_id="gen.broken",
        reason="x",
        forwarded_message="m",
    ))
    invoker = StubInvoker(InvocationResult(
        capability_id="gen.broken", kind="agent",
        output={}, used_mock=False, error="boom: kaboom", elapsed_ms=10,
    ))
    orch = TurnOrchestrator(
        StubLLM(), store, default_model="m",
        triage=triage, receptionist=StubReceptionist(store),
        invoker=invoker,
    )
    events = [e async for e in orch.turn(sid, "go")]
    token_evt = next(e for e in events if e["type"] == "token")
    assert "调用 gen.broken 失败" in token_evt["value"]


@pytest.mark.asyncio
async def test_route_generated_agent_missing_target_id(store_with_session):
    store, sid = store_with_session
    triage = StubTriage(TriageDecision(
        target=TriageTarget.GENERATED_AGENT, target_id=None,  # impossible per invariant
        reason="?", forwarded_message="m",
    ))
    invoker = StubInvoker(InvocationResult(
        capability_id="x", kind="agent", output={}, used_mock=False, error=None, elapsed_ms=0,
    ))
    orch = TurnOrchestrator(
        StubLLM(), store, default_model="m",
        triage=triage, receptionist=StubReceptionist(store),
        invoker=invoker,
    )
    events = [e async for e in orch.turn(sid, "go")]
    # Should NOT call invoker
    assert invoker.calls == []
    token_evt = next(e for e in events if e["type"] == "token")
    assert "没给 target_id" in token_evt["value"]


# ---------- Intent router branch ----------
@pytest.mark.asyncio
async def test_route_intent_router_calls_planner(store_with_session):
    store, sid = store_with_session
    triage = StubTriage(TriageDecision(
        target=TriageTarget.INTENT_ROUTER, target_id=None,
        reason="完整 SOP", forwarded_message="ocr 发票 → 总结金额",
    ))
    spec = WorkflowSpec(
        workflow_id="wf.demo", version="0.1.0",
        steps=[WorkflowStep(id="s1", capability="baidu.ocr.general_accurate")],
    )
    plan = IntentRouteResult(
        user_sop="ocr 发票 → 总结金额",
        intent_summary="OCR + 总结",
        sub_tasks=[IntentSubTask(task="ocr", keywords=["发票"])],
        candidate_shortlist=[],
        workflow_spec=spec,
        spec_valid=True,
        validation_errors=[],
        fallback_used=False,
        planner_attempts=1,
    )
    intent = StubIntentRouter(plan)
    orch = TurnOrchestrator(
        StubLLM(), store, default_model="m",
        triage=triage, receptionist=StubReceptionist(store),
        intent_router=intent,
    )
    events = [e async for e in orch.turn(sid, "ocr 发票 → 总结金额")]
    planned = next(e for e in events if e["type"] == "workflow_planned")
    assert planned["data"]["intent_summary"] == "OCR + 总结"
    assert planned["data"]["spec_valid"] is True
    assert planned["data"]["step_count"] == 1
    assert intent.calls == ["ocr 发票 → 总结金额"]


# ---------- SSE shape ----------
@pytest.mark.asyncio
async def test_turn_orchestrator_yields_triage_decision_first(store_with_session):
    store, sid = store_with_session
    triage = StubTriage(TriageDecision(
        target=TriageTarget.HELP, target_id=None,
        reason="r", forwarded_message="m",
    ))
    orch = TurnOrchestrator(
        StubLLM(), store, default_model="m",
        triage=triage, receptionist=StubReceptionist(store),
    )
    events = [e async for e in orch.turn(sid, "what?")]
    assert events[0]["type"] == "triage_decision"
    assert events[-1]["type"] == "turn_end"
    # All event payloads must be JSON-encodable (so SSE serialization works)
    for e in events:
        json.dumps(e, ensure_ascii=False, default=str)


@pytest.mark.asyncio
async def test_audit_log_records_triage_decision(store_with_session):
    """Batch K — every turn appends a triage_decision entry to session.audit_log."""
    store, sid = store_with_session
    triage = StubTriage(TriageDecision(
        target=TriageTarget.HELP, target_id=None,
        reason="meta question", forwarded_message="info",
    ))
    orch = TurnOrchestrator(
        StubLLM(), store, default_model="m",
        triage=triage, receptionist=StubReceptionist(store),
    )
    async for _ in orch.turn(sid, "Agent Ops 是什么?"):
        pass
    sess = await store.get(sid)
    triage_entries = [e for e in sess.audit_log if e["kind"] == "triage_decision"]
    assert len(triage_entries) == 1
    entry = triage_entries[0]
    assert entry["data"]["target"] == "help"
    assert entry["data"]["user_message"] == "Agent Ops 是什么?"
    assert entry["data"]["reason"] == "meta question"
    assert "ts" in entry


@pytest.mark.asyncio
async def test_agent_invocation_uses_input_coercion_for_schema(store_with_session, monkeypatch):
    """Batch I-2 — when the target agent declares an input_schema, the coercer
    fills it before invoking. The audit log records which coercion method was used.
    """
    from app.core.dialog import turn_orchestrator as orch_mod
    from app.registry.registry_hub import RegistryEntry

    fake_entry = RegistryEntry(
        kind="agent", id="gen.with-schema", version="0.1.0",
        name="Schema Demo", description="needs structured input",
        intent_keywords=[], tags=[], activation_status="active",
        is_generated=True,
        raw={
            "input_schema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            }
        },
    )
    monkeypatch.setattr(orch_mod, "get_entry",
                        lambda kind, eid: fake_entry if eid == "gen.with-schema" else None)

    store, sid = store_with_session
    triage = StubTriage(TriageDecision(
        target=TriageTarget.GENERATED_AGENT, target_id="gen.with-schema",
        reason="r", forwarded_message="python web scraping",
    ))
    invoker = StubInvoker(InvocationResult(
        capability_id="gen.with-schema", kind="agent",
        output={"ok": True}, used_mock=False, error=None, elapsed_ms=5,
    ))
    orch = TurnOrchestrator(
        StubLLM(), store, default_model="m",
        triage=triage, receptionist=StubReceptionist(store), invoker=invoker,
    )
    async for _ in orch.turn(sid, "用 gen.with-schema 跑一下"):
        pass
    # Coercer used heuristic (single 'query' string key) — bypasses LLM
    assert invoker.calls == [("gen.with-schema", {"query": "python web scraping"})]
    # Audit log carries the method label
    sess = await store.get(sid)
    invoked = [e for e in sess.audit_log if e["kind"] == "agent_invoked"][0]
    assert invoked["data"]["input_coercion_method"] == "heuristic"
    assert invoked["data"]["input_keys"] == ["query"]
    assert invoked["data"]["input_missing_required"] == []


@pytest.mark.asyncio
async def test_agent_invocation_with_no_schema_uses_default_message_shape(store_with_session, monkeypatch):
    """Empty schema → coercer returns {"message": forwarded_message}."""
    from app.core.dialog import turn_orchestrator as orch_mod
    from app.registry.registry_hub import RegistryEntry

    monkeypatch.setattr(orch_mod, "get_entry", lambda kind, eid: RegistryEntry(
        kind="agent", id=eid, version="0.1.0", name="x", description="x",
        intent_keywords=[], tags=[], activation_status="active",
        is_generated=True, raw={"input_schema": {}},
    ))
    store, sid = store_with_session
    triage = StubTriage(TriageDecision(
        target=TriageTarget.GENERATED_AGENT, target_id="gen.no-schema",
        reason="r", forwarded_message="hello world",
    ))
    invoker = StubInvoker(InvocationResult(
        capability_id="gen.no-schema", kind="agent",
        output={}, used_mock=True, error=None, elapsed_ms=1,
    ))
    orch = TurnOrchestrator(
        StubLLM(), store, default_model="m",
        triage=triage, receptionist=StubReceptionist(store), invoker=invoker,
    )
    async for _ in orch.turn(sid, "trigger"):
        pass
    assert invoker.calls == [("gen.no-schema", {"message": "hello world"})]
    sess = await store.get(sid)
    invoked = [e for e in sess.audit_log if e["kind"] == "agent_invoked"][0]
    assert invoked["data"]["input_coercion_method"] == "empty_schema"


@pytest.mark.asyncio
async def test_audit_log_records_agent_invoked(store_with_session):
    store, sid = store_with_session
    triage = StubTriage(TriageDecision(
        target=TriageTarget.GENERATED_AGENT, target_id="gen.x",
        reason="r", forwarded_message="m",
    ))
    invoker = StubInvoker(InvocationResult(
        capability_id="gen.x", kind="agent",
        output={"hello": "world"}, used_mock=False, error=None, elapsed_ms=42,
    ))
    orch = TurnOrchestrator(
        StubLLM(), store, default_model="m",
        triage=triage, receptionist=StubReceptionist(store),
        invoker=invoker,
    )
    async for _ in orch.turn(sid, "go"):
        pass
    sess = await store.get(sid)
    invoked = [e for e in sess.audit_log if e["kind"] == "agent_invoked"]
    assert len(invoked) == 1
    assert invoked[0]["data"]["agent_id"] == "gen.x"
    assert invoked[0]["data"]["elapsed_ms"] == 42


@pytest.mark.asyncio
async def test_audit_log_records_workflow_planned(store_with_session):
    store, sid = store_with_session
    triage = StubTriage(TriageDecision(
        target=TriageTarget.INTENT_ROUTER, target_id=None,
        reason="r", forwarded_message="ocr 发票",
    ))
    spec = WorkflowSpec(
        workflow_id="wf.demo", version="0.1.0",
        steps=[
            WorkflowStep(id="s1", capability="baidu.ocr.general_accurate"),
            WorkflowStep(id="s2", capability="analysis.report.docanalyze"),
        ],
    )
    plan = IntentRouteResult(
        user_sop="ocr 发票", intent_summary="OCR + summary",
        sub_tasks=[IntentSubTask(task="ocr", keywords=["发票"])],
        candidate_shortlist=[], workflow_spec=spec,
        spec_valid=True, validation_errors=[],
        fallback_used=False, planner_attempts=1,
    )
    orch = TurnOrchestrator(
        StubLLM(), store, default_model="m",
        triage=triage, receptionist=StubReceptionist(store),
        intent_router=StubIntentRouter(plan),
    )
    async for _ in orch.turn(sid, "ocr 发票"):
        pass
    sess = await store.get(sid)
    planned = [e for e in sess.audit_log if e["kind"] == "workflow_planned"]
    assert len(planned) == 1
    assert planned[0]["data"]["step_count"] == 2
    assert planned[0]["data"]["spec_valid"] is True
    assert "baidu.ocr.general_accurate" in planned[0]["data"]["step_capabilities"]


@pytest.mark.asyncio
async def test_turn_orchestrator_unknown_session_raises_keyerror(store_with_session):
    store, _ = store_with_session
    triage = StubTriage(TriageDecision(
        target=TriageTarget.HELP, target_id=None,
        reason="r", forwarded_message="m",
    ))
    orch = TurnOrchestrator(
        StubLLM(), store, default_model="m",
        triage=triage, receptionist=StubReceptionist(store),
    )
    with pytest.raises(KeyError):
        async for _ in orch.turn("does-not-exist", "hi"):
            pass
