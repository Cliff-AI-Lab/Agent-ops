"""IntentParserImpl - LLM-backed Natural Language -> StructuredIntent.

Strategy:
  1. Build system prompt with the StructuredIntent JSON Schema.
  2. Call LLM (task_type='结构化抽取', size='中') asking for ONLY a JSON object.
  3. Parse + Pydantic-validate. On validation failure: 1 retry with the
     error fed back to the LLM as an "error correction" turn.
  4. Emit trace events at each step.

The component does NOT bind atoms; it produces an abstract StructuredIntent
with `verb` strings and `suggested_subcategory` hints. Resolver does the
atom binding in stage (2).
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from app.core.llm.client import ChatMessage, LLMClient
from app.core.llm.model_router import ModelRouter
from app.core.pipelines.factory.ir import StructuredIntent
from app.core.trace.bus import emit


_INTENT_SCHEMA_HINT = """
你必须以一个 JSON 对象返回，结构如下（schema_version 固定 "1.0"）：
{
  "schema_version": "1.0",
  "goal": "<用户需求一句话总结>",
  "trigger": {
    "type": "cron" | "webhook" | "manual" | "event",
    "cron_expr": "<cron 表达式，type=cron 时必填>",
    "natural_language": "<原始时间表达，如 '每周一上午9点'>"
  },
  "steps": [
    {
      "id": "s1",
      "verb": "<抽象动作，如 '查询数据库' / '撰写中文周报'，不要具体技术名>",
      "inputs": {"<下游入参名>": "$<上游step.output>"},
      "expected_output_kind": "tabular_data | json_object | natural_language | markdown_text | void",
      "constraints": {"<任意键>": "<约束>"},
      "suggested_subcategory": "DB | HTTP | LLM | Notify | Schedule | OCR | TTS | ASR | VectorDB | Embedding | DocParser | Chart | WebSearch"
    }
  ],
  "outputs": [
    {"name": "<变量>", "type": "string|json|file|url|void", "sink": "dingtalk:运营群"}
  ],
  "constraints": {
    "language": "zh",
    "privacy": "internal"
  },
  "industry": {"code": "01", "primary": "通用", "sub": "商业运营"},
  "raw_user_input": "<原始NL，逐字保留>"
}

铁律：
- 步骤 id 用 s1, s2, s3... 顺序
- inputs 引用上游用 "$s1.output" 形式
- 不允许出现具体实现名（不要写"百度OCR"，写"识别图片文字"）
- 输出**纯 JSON**，不要 markdown 代码围栏，不要解释文字
"""

_SYSTEM_PROMPT = (
    "你是一个智能体工厂的 IntentParser，"
    "把用户的自然语言需求解析为结构化意图（StructuredIntent）。"
    + _INTENT_SCHEMA_HINT
)


class IntentParserImpl:
    """Real LLM-backed IntentParser."""

    def __init__(
        self,
        llm_client: LLMClient | None = None,
        model_router: ModelRouter | None = None,
        max_repair_attempts: int = 1,
    ) -> None:
        self._llm = llm_client
        self._router = model_router or ModelRouter()
        self._max_repair = max_repair_attempts

    async def parse(self, nl: str) -> StructuredIntent:
        if not nl or not nl.strip():
            raise ValueError("IntentParser.parse: nl must be non-empty.")

        emit("L3", "IntentParser", "parse_start", f"nl_len={len(nl)}")

        if self._llm is None:
            self._llm = LLMClient()

        model = self._router.resolve("结构化抽取", "中", ["json_response_format"])

        messages: list[ChatMessage] = [
            ChatMessage(role="system", content=_SYSTEM_PROMPT),
            ChatMessage(role="user", content=nl),
        ]

        last_error: Exception | None = None
        for attempt in range(1 + self._max_repair):
            response = await self._llm.chat(
                model=model,
                messages=messages,
                temperature=0.1,
                max_tokens=2000,
            )
            raw = response.content.strip()
            try:
                payload = self._extract_json(raw)
                payload.setdefault("raw_user_input", nl)
                intent = StructuredIntent.model_validate(payload)
                emit(
                    "L3",
                    "IntentParser",
                    "parse_ok",
                    f"steps={len(intent.steps)} attempt={attempt + 1}",
                )
                return intent
            except (ValidationError, json.JSONDecodeError, ValueError) as exc:
                last_error = exc
                emit(
                    "L3",
                    "IntentParser",
                    "parse_retry",
                    f"attempt={attempt + 1} err={type(exc).__name__}: {str(exc)[:120]}",
                )
                if attempt < self._max_repair:
                    messages.append(ChatMessage(role="assistant", content=raw))
                    messages.append(
                        ChatMessage(
                            role="user",
                            content=(
                                f"上一次输出无法解析 / 校验。错误：{exc}。"
                                f"请只返回**纯 JSON**，不要 markdown 围栏，不要解释，"
                                f"严格遵守 schema。"
                            ),
                        )
                    )

        emit("L3", "IntentParser", "parse_fail", f"final_err={last_error}")
        raise ValueError(
            f"IntentParser failed after {self._max_repair + 1} attempts. "
            f"Last error: {last_error}"
        )

    @staticmethod
    def _extract_json(raw: str) -> dict[str, Any]:
        """Strip markdown fences if any, parse JSON."""
        s = raw.strip()
        if s.startswith("```"):
            lines = s.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            s = "\n".join(lines).strip()
        if not s.startswith("{"):
            raise ValueError(f"Not a JSON object: starts with {s[:30]!r}")
        return json.loads(s)
