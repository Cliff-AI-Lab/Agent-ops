from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ValidationError

from app.core.llm.client import ChatMessage, LLMClient
from app.core.stability.contracts import RequirementSpec
from app.core.stability.repair import generate_with_repair
from app.core.stability.validator import SchemaValidator

Intent = Literal["ui", "agent", "tool", "unknown"]


class IntentResult(BaseModel):
    """Intent classification result for a user SOP."""

    intent: Intent
    confidence: float


class MetaAgent:
    """Classifies intent, then fills RequirementSpec via L4 repair loop."""

    def __init__(self, llm: LLMClient, light_model: str, heavy_model: str) -> None:
        self._llm = llm
        self._light_model = light_model
        self._heavy_model = heavy_model

    async def classify_intent(self, user_sop: str, history: list[str]) -> IntentResult:
        """Use the light model to classify UI generation intents."""
        validator = SchemaValidator(IntentResult)
        try:
            reply = await self._llm.chat(
                self._light_model,
                [
                    ChatMessage(
                        role="system",
                        content=(
                            "Classify the request intent. Return JSON only with keys "
                            '"intent" and "confidence".'
                        ),
                    ),
                    ChatMessage(
                        role="user",
                        content=f"SOP:\n{user_sop}\n\nHistory:\n{_render_history(history)}",
                    ),
                ],
                temperature=0.0,
            )
            result = validator.parse(reply.content)
        except (ValidationError, Exception):
            result = _heuristic_intent(user_sop, history)

        if result.intent != "ui" or result.confidence < 0.5:
            return IntentResult(intent="unknown", confidence=min(result.confidence, 0.49))
        return result

    async def build_spec(self, user_sop: str, history: list[str]) -> RequirementSpec:
        """Use the heavy model and repair loop to produce a RequirementSpec."""
        result = await generate_with_repair(
            self._llm,
            model=self._heavy_model,
            system_prompt="Produce a RequirementSpec from the SOP and dialog history.",
            user_prompt=f"SOP:\n{user_sop}\n\nHistory:\n{_render_history(history)}",
            schema=RequirementSpec,
        )
        return result.value


def _heuristic_intent(user_sop: str, history: list[str]) -> IntentResult:
    text = f"{user_sop}\n{' '.join(history)}".lower()
    ui_keywords = ("ui", "页面", "界面", "dashboard", "frontend", "前端", "设计")
    if any(keyword in text for keyword in ui_keywords):
        return IntentResult(intent="ui", confidence=0.6)
    return IntentResult(intent="unknown", confidence=0.0)


def _render_history(history: list[str]) -> str:
    return "\n".join(history) if history else "(empty)"
