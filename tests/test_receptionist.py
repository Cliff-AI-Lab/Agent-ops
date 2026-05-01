from __future__ import annotations

import pytest

from app.core.dialog.receptionist import Receptionist
from app.core.dialog.session import SessionStore
from app.core.dialog.state_machine import DialogState
from app.core.llm.client import ChatMessage


class FakeLLM:
    """A deterministic fake LLM for receptionist tests."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = responses
        self.call_count = 0

    async def chat(
        self,
        model: str,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, object]] | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> ChatMessage:
        _ = (model, messages, tools, temperature)
        response = self._responses[self.call_count]
        self.call_count += 1
        return ChatMessage(role="assistant", content=response)


@pytest.mark.asyncio
async def test_start_creates_collecting_session(tmp_path) -> None:
    receptionist = Receptionist(
        FakeLLM([]),
        SessionStore(str(tmp_path / "sessions.db")),
        default_model="gpt-4o-mini",
    )

    session = await receptionist.start("做一个门店运营后台")

    assert session.state == DialogState.COLLECTING
    assert session.messages[0].content == "做一个门店运营后台"


@pytest.mark.asyncio
async def test_turn_advances_state_and_preserves_sse_order(tmp_path) -> None:
    receptionist = Receptionist(
        FakeLLM(
            [
                "你希望这个后台主要给哪些角色使用？核心页面有哪些？",
                (
                    '{"product_name":"门店运营后台","target_users":["店长"],"core_pages":["首页","报表"],'
                    '"reference_brands":[],"special_requirements":["中文"]}'
                ),
            ]
        ),
        SessionStore(str(tmp_path / "sessions.db")),
        default_model="gpt-4o-mini",
    )
    session = await receptionist.start("做一个门店运营后台")

    first_events = [event async for event in receptionist.turn(session.id, "给店长使用")]
    second_events = [event async for event in receptionist.turn(session.id, "需要首页和报表页")]

    stored = await receptionist._store.get(session.id)

    assert first_events[0]["type"] == "state"
    assert first_events[1]["type"] == "token"
    assert first_events[-1]["type"] == "turn_end"
    assert first_events[0]["value"] == DialogState.CLARIFYING.value
    assert second_events[0]["value"] == DialogState.CONFIRMING.value
    assert stored is not None
    assert stored.state == DialogState.CONFIRMING
