"""TriageAgent tests (Batch Z++)."""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.core.llm.client import ChatMessage
from app.core.orchestrator.triage import (
    TriageAgent,
    TriageDecision,
    TriageTarget,
    _enforce_target_id_invariant,
)


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


# ---------- Schema invariants ----------
def test_triage_decision_strict_extra_forbid():
    with pytest.raises(Exception):
        TriageDecision(
            target=TriageTarget.HELP, target_id=None,
            reason="x", forwarded_message="y",
            extra_field="forbidden",  # type: ignore[call-arg]
        )


def test_enforce_target_id_invariant_strips_id_when_not_generated_agent():
    d = TriageDecision(
        target=TriageTarget.RECEPTIONIST, target_id="gen.something",
        reason="r", forwarded_message="m",
    )
    out = _enforce_target_id_invariant(d, candidate_ids=["gen.something"])
    assert out.target_id is None


def test_enforce_target_id_invariant_downgrades_unknown_gen_target():
    d = TriageDecision(
        target=TriageTarget.GENERATED_AGENT, target_id="gen.does-not-exist",
        reason="r", forwarded_message="m",
    )
    out = _enforce_target_id_invariant(d, candidate_ids=["gen.real-1"])
    assert out.target == TriageTarget.RECEPTIONIST
    assert out.target_id is None
    assert "downgraded" in out.reason


def test_enforce_target_id_invariant_keeps_valid_gen_target():
    d = TriageDecision(
        target=TriageTarget.GENERATED_AGENT, target_id="gen.real-1",
        reason="r", forwarded_message="m",
    )
    out = _enforce_target_id_invariant(d, candidate_ids=["gen.real-1"])
    assert out.target == TriageTarget.GENERATED_AGENT
    assert out.target_id == "gen.real-1"


# ---------- TriageAgent.route end-to-end ----------
@pytest.mark.asyncio
async def test_route_to_receptionist_for_new_product_request():
    payload = {
        "target": "receptionist",
        "target_id": None,
        "reason": "用户在描述新产品需求",
        "forwarded_message": "想做一个发票识别工具",
    }
    fake = FakeLLM([json.dumps(payload)])
    agent = TriageAgent(fake, model="haiku")
    result = await agent.route("我想做一个发票识别工具")
    assert result.decision.target == TriageTarget.RECEPTIONIST
    assert result.decision.target_id is None
    assert result.fallback_used is False
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_route_to_intent_router_for_complete_sop():
    payload = {
        "target": "intent_router",
        "target_id": None,
        "reason": "消息已经是一段完整 SOP",
        "forwarded_message": "扫描发票 OCR → LLM 提取金额",
    }
    fake = FakeLLM([json.dumps(payload)])
    agent = TriageAgent(fake, model="haiku")
    result = await agent.route("帮我跑这个流程: 扫描发票 OCR → LLM 提取金额")
    assert result.decision.target == TriageTarget.INTENT_ROUTER
    assert result.decision.target_id is None


@pytest.mark.asyncio
async def test_route_to_help_for_meta_question():
    payload = {
        "target": "help",
        "target_id": None,
        "reason": "用户问的是平台用法",
        "forwarded_message": "Agent Ops 用法介绍",
    }
    fake = FakeLLM([json.dumps(payload)])
    agent = TriageAgent(fake, model="haiku")
    result = await agent.route("Agent Ops 是什么?")
    assert result.decision.target == TriageTarget.HELP


@pytest.mark.asyncio
async def test_route_to_generated_agent_with_strict_validation(monkeypatch):
    """Triage chooses a gen.* agent that EXISTS in the registry — kept as-is."""
    from app.core.orchestrator import triage as triage_mod

    monkeypatch.setattr(
        triage_mod, "_list_active_generated_agents",
        lambda: [{
            "agent_id": "gen.weekly-1",
            "name": "Weekly",
            "description": "周报生成",
            "intent_keywords": ["周报"],
        }],
    )
    payload = {
        "target": "generated_agent",
        "target_id": "gen.weekly-1",
        "reason": "用户调用周报生成 agent",
        "forwarded_message": "生成本周周报",
    }
    fake = FakeLLM([json.dumps(payload)])
    agent = TriageAgent(fake, model="haiku")
    result = await agent.route("用之前那个周报工具帮我生成本周周报")
    assert result.decision.target == TriageTarget.GENERATED_AGENT
    assert result.decision.target_id == "gen.weekly-1"
    assert "gen.weekly-1" in result.candidate_agent_ids


@pytest.mark.asyncio
async def test_route_hallucinated_target_id_is_downgraded(monkeypatch):
    """If LLM picks a gen.* id that doesn't exist, downgrade to receptionist."""
    from app.core.orchestrator import triage as triage_mod

    monkeypatch.setattr(triage_mod, "_list_active_generated_agents", lambda: [])
    payload = {
        "target": "generated_agent",
        "target_id": "gen.invented-by-llm",
        "reason": "claims to use a fake agent",
        "forwarded_message": "go",
    }
    fake = FakeLLM([json.dumps(payload)])
    agent = TriageAgent(fake, model="haiku")
    result = await agent.route("用 invented agent")
    assert result.decision.target == TriageTarget.RECEPTIONIST
    assert result.decision.target_id is None
    assert "downgraded" in result.decision.reason


@pytest.mark.asyncio
async def test_route_repair_path_on_invalid_json():
    """Malformed first reply → Repair retries → second reply valid."""
    invalid = "not json at all"
    valid = json.dumps({
        "target": "receptionist", "target_id": None,
        "reason": "ok", "forwarded_message": "好的",
    })
    fake = FakeLLM([invalid, valid])
    agent = TriageAgent(fake, model="haiku")
    result = await agent.route("hi")
    assert result.decision.target == TriageTarget.RECEPTIONIST
    assert result.attempts == 2
    assert len(fake.calls) == 2


@pytest.mark.asyncio
async def test_route_emits_trace_events(monkeypatch):
    from app.core.orchestrator import triage as triage_mod

    captured: list[tuple[str, str, str]] = []
    real_emit = triage_mod.emit

    def spy(layer, component, kind, message, **kwargs):
        captured.append((component, kind, message))
        return real_emit(layer, component, kind, message, **kwargs)

    monkeypatch.setattr(triage_mod, "emit", spy)
    payload = {
        "target": "help", "target_id": None,
        "reason": "meta", "forwarded_message": "info",
    }
    fake = FakeLLM([json.dumps(payload)])
    agent = TriageAgent(fake, model="haiku")
    await agent.route("what is this")
    kinds = [k for (comp, k, _msg) in captured if comp == "Triage"]
    assert "start" in kinds
    assert "decision" in kinds


@pytest.mark.asyncio
async def test_route_rejects_empty_message():
    fake = FakeLLM([])
    agent = TriageAgent(fake, model="haiku")
    with pytest.raises(ValueError):
        await agent.route("   ")
    assert len(fake.calls) == 0
