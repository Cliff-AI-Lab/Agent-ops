"""TurnOrchestrator — wires TriageAgent into the live dialog turn flow (Batch I).

Every incoming user message in /api/sessions/{id}/turn now runs:

    1. Triage (light LLM) → TriageDecision
    2. Branch on decision.target:
         receptionist     → existing Receptionist multi-turn dialog
         generated_agent  → CapabilityInvoker.invoke() into the chosen gen.* agent
         intent_router    → IntentRouter.plan() — return a candidate WorkflowSpec
         help             → templated help message (no LLM)

This is the user-visible realization of the self-reinforcing loop:
typing "用之前那个周报工具帮我生成本周周报" finds and invokes the previously
generated agent inside the same dialog window.

Streaming contract — emits SSE-shaped event dicts:

    {"type": "triage_decision", "data": { ... }}      — always first
    {"type": "state",           "value": "..."}        — when state changed
    {"type": "token",           "value": "..."}        — assistant text chunks
    {"type": "agent_invoked",   "data": { ... }}       — generated_agent branch
    {"type": "workflow_planned","data": { ... }}       — intent_router branch
    {"type": "turn_end"}                                — always last

The orchestrator owns the user-message append so that branches don't double-write
the dialog log.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime

from app.core.dialog.agent_input_coercer import coerce_inputs
from app.core.dialog.receptionist import Receptionist
from app.core.dialog.session import Session, SessionStore
from app.core.llm.client import ChatMessage, LLMClient
from app.registry.registry_hub import get_entry
from app.core.orchestrator.intent_router import IntentRouter
from app.core.orchestrator.triage import (
    TriageAgent,
    TriageRouteResult,
    TriageTarget,
)
from app.core.trace.bus import emit
from app.core.workflow_engine.invoker import CapabilityInvoker

HELP_MESSAGE = (
    "👋 这里是 Agent Ops 平台。你可以:\n"
    "• 用一句话描述想要的产品(如「做一个发票识别小工具」),我来引导你完善需求\n"
    "• 直接给一段完整 SOP,我可以拼装一份可审计的 WorkflowSpec\n"
    "• 召唤之前生成过的智能体,例如「用上次那个周报工具帮我生成本周周报」\n"
    "随时输入 /help 获取这条说明。"
)


class TurnOrchestrator:
    """Front-of-flow router for /api/sessions/{id}/turn.

    Run-time dependencies are injected so unit tests can sub in fakes.
    """

    def __init__(
        self,
        llm: LLMClient,
        store: SessionStore,
        *,
        default_model: str,
        triage_model: str | None = None,
        triage: TriageAgent | None = None,
        intent_router: IntentRouter | None = None,
        invoker: CapabilityInvoker | None = None,
        receptionist: Receptionist | None = None,
    ) -> None:
        self._llm = llm
        self._store = store
        self._default_model = default_model
        self._triage_model = triage_model or default_model
        self._triage = triage or TriageAgent(llm, model=self._triage_model)
        self._intent_router = intent_router or IntentRouter(
            llm, light_model=self._triage_model, heavy_model=default_model,
        )
        self._invoker = invoker or CapabilityInvoker(mock_when_missing=True)
        self._receptionist = receptionist or Receptionist(llm, store, default_model)

    async def turn(
        self,
        session_id: str,
        user_msg: str,
    ) -> AsyncIterator[dict[str, object]]:
        """Yield SSE events for one user turn after triage routing."""
        session = await self._store.get(session_id)
        if session is None:
            raise KeyError(session_id)

        summary = _build_session_summary(session)
        triage_result = await self._triage.route(user_msg, session_summary=summary)
        decision = triage_result.decision

        emit(
            "L2", "TurnOrchestrator", "route",
            f"target={decision.target.value} · target_id={decision.target_id or '-'}",
            session_id=session.id,
        )
        # Batch K — audit-trail entry persisted per turn
        _audit_append(session, "triage_decision", {
            "target": decision.target.value,
            "target_id": decision.target_id,
            "reason": decision.reason,
            "forwarded_message": decision.forwarded_message,
            "fallback_used": triage_result.fallback_used,
            "user_message": user_msg,
        })
        await self._store.save(session)
        yield {
            "type": "triage_decision",
            "data": {
                "target": decision.target.value,
                "target_id": decision.target_id,
                "reason": decision.reason,
                "fallback_used": triage_result.fallback_used,
            },
        }

        target = decision.target
        if target == TriageTarget.RECEPTIONIST:
            # Receptionist owns its own session.messages.append
            async for event in self._receptionist.turn(session_id, user_msg):
                yield event
            return

        # Other branches: orchestrator owns the session-message bookkeeping.
        session.messages.append(ChatMessage(role="user", content=user_msg))

        if target == TriageTarget.HELP:
            assistant_text = HELP_MESSAGE
            session.messages.append(ChatMessage(role="assistant", content=assistant_text))
            await self._store.save(session)
            yield {"type": "token", "value": assistant_text}
            yield {"type": "turn_end"}
            return

        if target == TriageTarget.GENERATED_AGENT:
            async for event in self._invoke_generated_agent(session, decision.target_id, decision.forwarded_message):
                yield event
            return

        if target == TriageTarget.INTENT_ROUTER:
            async for event in self._plan_workflow(session, decision.forwarded_message):
                yield event
            return

        # Should be unreachable thanks to the enum, but be defensive:
        assistant_text = f"未识别的路由目标: {target}"
        session.messages.append(ChatMessage(role="assistant", content=assistant_text))
        await self._store.save(session)
        yield {"type": "token", "value": assistant_text}
        yield {"type": "turn_end"}

    # -------------------- branches --------------------
    async def _invoke_generated_agent(
        self,
        session: Session,
        target_id: str | None,
        forwarded_message: str,
    ) -> AsyncIterator[dict[str, object]]:
        if not target_id:
            assistant_text = "Triage 选了 generated_agent 但没给 target_id,跳过执行。"
            session.messages.append(ChatMessage(role="assistant", content=assistant_text))
            await self._store.save(session)
            yield {"type": "token", "value": assistant_text}
            yield {"type": "turn_end"}
            return

        # Batch I-2 — coerce the user message into the agent's declared input_schema.
        agent_entry = get_entry("agent", target_id)
        agent_input_schema = (agent_entry.raw or {}).get("input_schema") if agent_entry else None
        coercion = await coerce_inputs(
            forwarded_message,
            agent_input_schema if isinstance(agent_input_schema, dict) else None,
            llm=self._llm,
            model=self._triage_model,
        )
        inputs = coercion.inputs
        result = await self._invoker.invoke(target_id, inputs, kind_hint="agent")

        if result.error:
            assistant_text = (
                f"⚠ 调用 {target_id} 失败: {result.error}\n"
                f"已记录到 trace,稍后可重试。"
            )
        else:
            preview = _preview_output(result.output)
            assistant_text = (
                f"✅ 调用 {target_id} 完成{(' (mock)' if result.used_mock else '')}\n"
                f"耗时: {result.elapsed_ms}ms\n"
                f"输出预览: {preview}"
            )

        _audit_append(session, "agent_invoked", {
            "agent_id": result.capability_id,
            "kind": result.kind,
            "used_mock": result.used_mock,
            "error": result.error,
            "elapsed_ms": result.elapsed_ms,
            "input_keys": sorted(inputs.keys()),
            "input_coercion_method": coercion.method,
            "input_missing_required": coercion.missing_required,
        })
        session.messages.append(ChatMessage(role="assistant", content=assistant_text))
        await self._store.save(session)
        yield {
            "type": "agent_invoked",
            "data": {
                "agent_id": result.capability_id,
                "kind": result.kind,
                "output": result.output,
                "used_mock": result.used_mock,
                "error": result.error,
                "elapsed_ms": result.elapsed_ms,
            },
        }
        yield {"type": "token", "value": assistant_text}
        yield {"type": "turn_end"}

    async def _plan_workflow(
        self,
        session: Session,
        forwarded_message: str,
    ) -> AsyncIterator[dict[str, object]]:
        plan = await self._intent_router.plan(forwarded_message)
        spec = plan.workflow_spec
        steps_repr = " → ".join(f"{s.id}:{s.capability}" for s in spec.steps) or "(空)"
        assistant_text = (
            f"📋 已为你拼装 WorkflowSpec(暂未执行,等你审核)\n"
            f"意图: {plan.intent_summary}\n"
            f"steps: {steps_repr}\n"
            f"shortlist: {len(plan.candidate_shortlist)} · "
            f"valid: {plan.spec_valid} · attempts: {plan.planner_attempts}"
        )
        _audit_append(session, "workflow_planned", {
            "intent_summary": plan.intent_summary,
            "step_count": len(spec.steps),
            "spec_valid": plan.spec_valid,
            "fallback_used": plan.fallback_used,
            "shortlist_size": len(plan.candidate_shortlist),
            "step_capabilities": [s.capability for s in spec.steps],
        })
        session.messages.append(ChatMessage(role="assistant", content=assistant_text))
        await self._store.save(session)
        yield {
            "type": "workflow_planned",
            "data": {
                "intent_summary": plan.intent_summary,
                "step_count": len(spec.steps),
                "spec_valid": plan.spec_valid,
                "fallback_used": plan.fallback_used,
                "shortlist_size": len(plan.candidate_shortlist),
                "workflow_spec": spec.model_dump(mode="json"),
            },
        }
        yield {"type": "token", "value": assistant_text}
        yield {"type": "turn_end"}


def _audit_append(session: Session, kind: str, data: dict[str, object]) -> None:
    """Append one audit entry to session.audit_log (Batch K).

    Mutation in place; SessionStore.save persists. Each entry is a flat dict
    so it round-trips through SQLite as JSON without schema fuss.
    """
    session.audit_log.append({
        "ts": datetime.utcnow().isoformat(),
        "kind": kind,
        "data": data,
    })


def _build_session_summary(session: Session) -> str:
    spec = session.requirement_spec or {}
    sop = next((m.content for m in session.messages if m.role == "user"), "")
    return (
        f"session_id={session.id[:8]} · state={session.state.value} · "
        f"messages={len(session.messages)} · sop={sop[:120]} · "
        f"product_name={spec.get('product_name', '-')} · "
        f"product_type={spec.get('product_type', '-')}"
    )


def _preview_output(output: dict, *, n: int = 240) -> str:
    import json as _json

    try:
        text = _json.dumps(output, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        text = repr(output)
    if len(text) <= n:
        return text
    return text[:n] + "…"


__all__ = ["TurnOrchestrator", "HELP_MESSAGE"]
