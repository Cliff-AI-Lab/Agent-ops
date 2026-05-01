from __future__ import annotations

from collections.abc import AsyncIterator

from app.core.dialog.session import Session, SessionStore
from app.core.dialog.state_machine import DialogState, transition
from app.core.llm.client import ChatMessage, LLMClient
from app.core.stability.contracts import RequirementSpec
from app.core.stability.repair import generate_with_repair
from app.core.trace.bus import emit

RECEPTIONIST_SYSTEM_PROMPT = """你是 Agent Harness 的需求接待员。职责:通过对话帮用户澄清产品需求,**不生成代码**、不提设计建议。

每次回复必须严格按以下 3 段结构输出,三个小标题必须原样出现,标题间用空行:

📋 **已理解**
用简短要点列出你已经从对话中确认的字段(不要猜测,只写用户明确说过或强烈暗示的),例如:
- 产品类型:xxx
- 核心页面:xxx(3-5 页名字)
- 目标用户:xxx
- 团队/规模/特殊要求:xxx(若有)
若某字段还不明,**不要列,不要写"未知"**。

❓ **还想了解**
列出 1-2 个(最多 2 个)最关键的待澄清问题,用短句。**不要超过 2 个问题**。

💡 **下一步**
一句话告诉用户:"继续回答问题可以让需求更精确,或者回复「差不多了」我就帮你生成需求摘要进入生成阶段。"

规则:
- 不要编造用户没说的信息
- 只用中文,语气专业简洁
- 三段标题用 emoji + **加粗**,标题下的内容用列表/短句"""


class Receptionist:
    """Collects and confirms user requirements through dialog turns."""

    def __init__(self, llm: LLMClient, store: SessionStore, default_model: str) -> None:
        self._llm = llm
        self._store = store
        self._default_model = default_model

    async def start(self, user_sop: str) -> Session:
        """Create session in COLLECTING state with user's initial SOP."""
        session = await self._store.create()
        session.messages.append(ChatMessage(role="user", content=user_sop))
        await self._store.save(session)
        emit("L2", "Receptionist", "session_start",
             f"new session · state=collecting",
             session_id=session.id,
             data={"sop_len": len(user_sop)})
        return session

    async def turn(self, session_id: str, user_msg: str) -> AsyncIterator[dict[str, str]]:
        """Advance the dialog one turn and emit SSE-shaped events."""
        session = await self._store.get(session_id)
        if session is None:
            raise KeyError(session_id)

        session.messages.append(ChatMessage(role="user", content=user_msg))
        proceed_requested = _is_proceed(user_msg)
        emit("L2", "Receptionist", "turn_start",
             f"state={session.state.value} · user_turns={sum(1 for m in session.messages if m.role == 'user')}"
             + (" · user requested proceed" if proceed_requested else ""),
             session_id=session.id)

        if session.state == DialogState.CONFIRMING:
            async for event in self._handle_confirming(session, user_msg):
                yield event
            return
        if session.state == DialogState.GENERATING:
            session.state = transition(session.state, DialogState.DELIVERED)
            assistant_text = "当前会话已完成交付状态标记。"
            session.messages.append(ChatMessage(role="assistant", content=assistant_text))
            await self._store.save(session)
            yield {"type": "state", "value": session.state.value}
            yield {"type": "token", "value": assistant_text}
            yield {"type": "turn_end"}
            return
        if session.state == DialogState.DELIVERED:
            assistant_text = "当前会话已结束，如需新需求请创建新会话。"
            session.messages.append(ChatMessage(role="assistant", content=assistant_text))
            await self._store.save(session)
            yield {"type": "state", "value": session.state.value}
            yield {"type": "token", "value": assistant_text}
            yield {"type": "turn_end"}
            return

        user_turns = sum(1 for message in session.messages if message.role == "user")
        if user_turns >= 3 or proceed_requested:
            emit("L3", "MetaAgent", "spec_fill",
                 f"filling RequirementSpec from {user_turns} user turns",
                 session_id=session.id)
            repair_result = await generate_with_repair(
                self._llm,
                model=self._default_model,
                system_prompt=(
                    "Summarize the dialog into a RequirementSpec. Infer only what is directly "
                    "supported by the conversation."
                ),
                user_prompt=_conversation_text(session),
                schema=RequirementSpec,
            )
            session.requirement_spec = repair_result.value.model_dump()
            prev = session.state.value
            session.state = transition(session.state, DialogState.CONFIRMING)
            emit("L2", "StateMachine", "transition",
                 f"{prev} → {session.state.value}",
                 session_id=session.id)
            assistant_text = _build_confirmation_text(repair_result.value)
        else:
            target_state = DialogState.CLARIFYING
            prev = session.state.value
            session.state = transition(session.state, target_state)
            emit("L2", "StateMachine", "transition",
                 f"{prev} → {session.state.value}",
                 session_id=session.id)
            assistant_text = await self._next_question(session)

        session.messages.append(ChatMessage(role="assistant", content=assistant_text))
        await self._store.save(session)

        yield {"type": "state", "value": session.state.value}
        yield {"type": "token", "value": assistant_text}
        yield {"type": "turn_end"}

    async def _handle_confirming(self, session: Session, user_msg: str) -> AsyncIterator[dict[str, str]]:
        if _is_confirmation(user_msg):
            generating = transition(session.state, DialogState.GENERATING)
            session.state = transition(generating, DialogState.DELIVERED)
            assistant_text = "需求已确认，当前 MVP 已标记为 delivered，等待后续生成链路接管。"
        else:
            session.state = transition(session.state, DialogState.CLARIFYING)
            assistant_text = "请告诉我需要调整的内容，我继续帮你补全需求摘要。"
        session.messages.append(ChatMessage(role="assistant", content=assistant_text))
        await self._store.save(session)
        yield {"type": "state", "value": session.state.value}
        yield {"type": "token", "value": assistant_text}
        yield {"type": "turn_end"}

    async def _next_question(self, session: Session) -> str:
        reply = await self._llm.chat(
            self._default_model,
            [
                ChatMessage(role="system", content=RECEPTIONIST_SYSTEM_PROMPT),
                *session.messages,
                ChatMessage(
                    role="user",
                    content="基于以上对话，只追问 1-2 个最关键的澄清问题，不要输出别的内容。",
                ),
            ],
            temperature=0.2,
        )
        return reply.content.strip()


def _conversation_text(session: Session) -> str:
    return "\n".join(f"{message.role}: {message.content}" for message in session.messages)


def _build_confirmation_text(spec: RequirementSpec) -> str:
    return (
        "请确认以下需求摘要:\n"
        f"产品名称: {spec.product_name}\n"
        f"目标用户: {', '.join(spec.target_users)}\n"
        f"核心页面: {', '.join(spec.core_pages)}\n"
        f"参考品牌: {', '.join(spec.reference_brands) or '无'}\n"
        f"特殊要求: {', '.join(spec.special_requirements) or '无'}\n"
        "回复“确认”即可进入下一阶段，或直接指出需要修改的地方。"
    )


def _is_confirmation(user_msg: str) -> bool:
    normalized = user_msg.strip().lower()
    keywords = {"确认", "ok", "okay", "yes", "y", "好的", "没问题"}
    return any(keyword in normalized for keyword in keywords)


def _is_proceed(user_msg: str) -> bool:
    """Detect user intent to skip further clarification and proceed."""
    normalized = user_msg.strip().lower()
    keywords = (
        "差不多了", "可以了", "开始生成", "直接生成", "就这些", "足够了", "我觉得可以",
        "go ahead", "let's go", "proceed", "generate now", "that's enough", "i'm ready",
    )
    return any(k in normalized for k in keywords)
