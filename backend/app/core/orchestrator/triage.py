"""TriageAgent — first-hop dynamic router (Batch Z++).

Borrowing from OpenAI's `cs-agents-demo` pattern but kept inside Agent Ops's
**deterministic shell**: only this very first hop is dynamic. Once Triage emits
a :class:`TriageDecision`, the rest of the system runs through audited, typed,
replayable paths (Receptionist / IntentRouter / generated agent invocation).

Usage:

    triage = TriageAgent(llm, model="claude-haiku-4-5-20251001")
    decision = await triage.route(
        user_message="再帮我做一个工时统计周报",
        session_summary="session id=abc, state=confirming, sop=团队周报小工具",
    )
    # decision.target → "intent_router"  (or receptionist / generated_agent / help)
    # decision.target_id → e.g. "gen.team-weekly-reporter-1cc29872" when target=generated_agent
    # decision.forwarded_message → potentially rephrased message to send to the target

The endpoint that consumes this decision is :mod:`app.api.triage`. The decision
is **only an audit boundary** — the TriageAgent never executes anything itself.
"""
from __future__ import annotations

import json
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.llm.client import LLMClient
from app.core.stability.repair import generate_with_repair
from app.core.trace.bus import emit


class TriageTarget(str, Enum):
    """Fixed set of destinations Triage may route to.

    Keeping this enum small is a feature: every new target is a deliberate
    extension to the deterministic shell.
    """

    RECEPTIONIST = "receptionist"        # 进 Receptionist 多轮对话填 RequirementSpec
    INTENT_ROUTER = "intent_router"      # 直接拿这条 SOP 跑 Planner 出 WorkflowSpec
    GENERATED_AGENT = "generated_agent"  # 转发给某个用户已生成的 gen.* agent
    HELP = "help"                        # 平台介绍 / 用法说明,不调任何 agent


class TriageDecision(BaseModel):
    """The triage agent's audited routing decision."""

    target: TriageTarget = Field(..., description="Where to route the user message.")
    target_id: str | None = Field(
        default=None,
        description=(
            "When target=generated_agent, the active gen.* agent_id to forward to. "
            "Must be None for other targets."
        ),
    )
    reason: str = Field(..., min_length=1, max_length=400)
    forwarded_message: str = Field(
        ...,
        min_length=1,
        description="Message to forward to the target (may rephrase the user input).",
    )

    model_config = ConfigDict(extra="forbid")

    @field_validator("target_id")
    @classmethod
    def _strip_target_id(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        return v or None


class TriageRouteResult(BaseModel):
    """Everything Triage produces for one message — auditable + replayable."""

    user_message: str
    decision: TriageDecision
    candidate_agent_ids: list[str] = Field(
        default_factory=list,
        description="Active gen.* agents that were considered.",
    )
    fallback_used: bool = False
    attempts: int = 1

    model_config = ConfigDict(extra="forbid")


SYSTEM_PROMPT = (
    "你是 Agent Ops 平台的前台调度员(TriageAgent)。每条用户消息都会先到你这里,"
    "你需要根据消息内容、可选的会话摘要、当前可用的生成 agent 清单,决定**只把这条消息**"
    "路由到哪一个目的地。\n\n"
    "目的地枚举(target):\n"
    "  • receptionist     — 用户在描述新需求/产品,需要进入接待员多轮对话填写 RequirementSpec\n"
    "  • intent_router    — 用户消息已经是一段完整 SOP,直接进 Planner 拼装 WorkflowSpec\n"
    "  • generated_agent  — 用户在召唤 / 调用某个之前生成的 agent,target_id 必须填该 agent_id\n"
    "  • help             — 用户问的是平台用法 / 介绍 / 元问题,不调任何 agent\n\n"
    "硬性规则:\n"
    "1. 只有当 target=generated_agent 时,target_id 才填一个具体 gen.* id;其它情况一律 null。\n"
    "2. target_id 必须来自'可用 agent 清单',**不准编造**。如果用户说'帮我用周报工具'但清单里没合适的,选 receptionist 或 intent_router。\n"
    "3. forwarded_message 是给下游 agent 的输入(可在用户原话基础上做轻量改写以更清楚)。\n"
    "4. reason 用 1-2 句话说明为什么这么路由。\n\n"
    "只输出 JSON,严格匹配 schema。"
)


def _format_active_agents(agents: list[dict]) -> str:
    if not agents:
        return "(暂无生成的 agent)"
    out: list[str] = []
    for a in agents:
        kw = a.get("intent_keywords") or []
        out.append(
            f"  - {a['agent_id']}  | name: {a.get('name', '')}\n"
            f"    keywords: {', '.join(kw[:8]) if kw else '(none)'}\n"
            f"    desc: {(a.get('description') or '')[:140]}"
        )
    return "\n".join(out)


class TriageAgent:
    """SOP / message → routing decision, with strict validator on target_id."""

    def __init__(
        self,
        llm: LLMClient,
        model: str,
        *,
        max_repairs: int = 2,
    ) -> None:
        self._llm = llm
        self._model = model
        self._max_repairs = max_repairs

    async def route(
        self,
        user_message: str,
        *,
        session_summary: str | None = None,
    ) -> TriageRouteResult:
        """Route a single message. Pure decision — never invokes the target."""
        if not user_message or not user_message.strip():
            raise ValueError("user_message cannot be empty")

        active_agents = _list_active_generated_agents()
        candidate_ids = [a["agent_id"] for a in active_agents]

        emit(
            "L3", "Triage", "start",
            f"msg_len={len(user_message)} · active_agents={len(active_agents)}",
        )

        user_prompt = (
            f"用户消息:\n{user_message}\n\n"
            f"会话摘要: {session_summary or '(无,新会话)'}\n\n"
            f"可用 agent 清单:\n{_format_active_agents(active_agents)}\n\n"
            f"请输出 TriageDecision JSON。"
        )

        try:
            result = await generate_with_repair(
                self._llm,
                model=self._model,
                system_prompt=SYSTEM_PROMPT,
                user_prompt=user_prompt,
                schema=TriageDecision,
                max_repairs=self._max_repairs,
                max_tokens=600,
            )
            decision = result.value
            attempts = result.attempts
            fallback_used = result.used_fallback
        except Exception as exc:  # noqa: BLE001
            emit(
                "L3", "Triage", "llm_failed",
                f"{type(exc).__name__}: {exc} → fallback to receptionist",
            )
            decision = TriageDecision(
                target=TriageTarget.RECEPTIONIST,
                target_id=None,
                reason="Triage LLM unavailable, defaulting to receptionist for safety.",
                forwarded_message=user_message,
            )
            attempts = 0
            fallback_used = True

        # Strict post-validate: enforce target_id contract regardless of LLM
        decision = _enforce_target_id_invariant(decision, candidate_ids)

        emit(
            "L3", "Triage", "decision",
            f"target={decision.target.value} · target_id={decision.target_id or '-'} · attempts={attempts}",
            data={
                "target": decision.target.value,
                "target_id": decision.target_id,
                "reason": decision.reason,
                "fallback_used": fallback_used,
            },
        )

        return TriageRouteResult(
            user_message=user_message,
            decision=decision,
            candidate_agent_ids=candidate_ids,
            fallback_used=fallback_used,
            attempts=attempts,
        )


def _list_active_generated_agents() -> list[dict]:
    """Return a compact list of every active gen.* agent currently in RegistryHub."""
    from app.registry.registry_hub import list_all

    out: list[dict] = []
    for entry in list_all(kind="agent", activation_status="active"):
        if not entry.id.startswith("gen."):
            continue
        out.append({
            "agent_id": entry.id,
            "name": entry.name,
            "description": entry.description,
            "intent_keywords": list(entry.intent_keywords),
        })
    return out


def _enforce_target_id_invariant(
    decision: TriageDecision,
    candidate_ids: list[str],
) -> TriageDecision:
    """Coerce the decision so target_id contract holds even if the LLM cheated.

    Rules:
      - target != generated_agent → target_id MUST be None
      - target == generated_agent → target_id MUST be in candidate_ids; otherwise
        downgrade to receptionist (safer than executing an invented id).
    """
    if decision.target != TriageTarget.GENERATED_AGENT:
        if decision.target_id is not None:
            return decision.model_copy(update={"target_id": None})
        return decision

    if decision.target_id and decision.target_id in candidate_ids:
        return decision

    # Hallucinated target_id — downgrade to receptionist
    return decision.model_copy(update={
        "target": TriageTarget.RECEPTIONIST,
        "target_id": None,
        "reason": (
            f"Original triage chose generated_agent={decision.target_id!r} "
            f"but no such active agent exists; downgraded to receptionist. "
            f"(LLM reason: {decision.reason})"
        )[:400],
    })


__all__ = [
    "TriageAgent",
    "TriageDecision",
    "TriageRouteResult",
    "TriageTarget",
]
