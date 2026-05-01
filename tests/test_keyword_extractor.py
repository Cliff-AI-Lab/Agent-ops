"""Tests for the LLM-driven retrieval-keyword extractor (Batch G)."""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.core.llm.client import ChatMessage
from app.marketplace.keyword_extractor import (
    IntentKeywordSet,
    _safe_seed_keywords,
    extract_intent_keywords,
)
from app.ontology import RequirementSpec


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


class CrashingLLM:
    """Always raises — exercises the fallback path."""

    async def chat(self, *args, **kwargs) -> ChatMessage:  # noqa: ANN002,ANN003
        raise RuntimeError("network down")


@pytest.fixture
def sample_spec() -> RequirementSpec:
    return RequirementSpec(
        product_name="Team Weekly Reporter",
        product_type="tool",
        target_users=["开发团队"],
        core_pages=["报告列表", "详情"],
        special_requirements=["从 GitHub 拉 commit", "调用 LLM 生成总结"],
    )


def test_intent_keyword_set_schema_validates():
    s = IntentKeywordSet(keywords=["a", "b", "c"])
    assert s.keywords == ["a", "b", "c"]
    with pytest.raises(Exception):
        IntentKeywordSet(keywords=["only-two", "kw"])  # min_length=3
    with pytest.raises(Exception):
        IntentKeywordSet(keywords=["x"] * 13)          # max_length=12


def test_safe_seed_keywords_uses_product_name_and_special_reqs(sample_spec):
    seed = _safe_seed_keywords(sample_spec)
    assert "Team Weekly Reporter" in seed
    assert any("GitHub" in s for s in seed)
    # Dedupe is case-insensitive
    assert len(seed) == len({s.lower() for s in seed})


@pytest.mark.asyncio
async def test_extract_keywords_happy_path(sample_spec):
    payload = {
        "keywords": [
            "周报生成", "团队周报", "weekly report",
            "team summary", "progress report", "工时统计",
        ],
    }
    llm = FakeLLM([json.dumps(payload)])
    out = await extract_intent_keywords(
        llm, model="haiku", spec=sample_spec,
        source_sop="给团队做一个周报小工具",
    )
    assert out == payload["keywords"]
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_extract_keywords_dedupes_case_insensitive(sample_spec):
    payload = {"keywords": ["周报", "周报", "Weekly Report", "weekly report", "Team"]}
    llm = FakeLLM([json.dumps(payload)])
    out = await extract_intent_keywords(llm, model="haiku", spec=sample_spec)
    # Case-insensitive dedupe; first occurrence wins
    assert out == ["周报", "Weekly Report", "Team"]


@pytest.mark.asyncio
async def test_extract_keywords_falls_back_when_llm_crashes(sample_spec):
    out = await extract_intent_keywords(
        CrashingLLM(), model="haiku", spec=sample_spec,
        source_sop="任意 sop",
    )
    # Falls back to seed (product_name + special_requirements + product_type)
    assert "Team Weekly Reporter" in out
    assert any("GitHub" in s for s in out)


@pytest.mark.asyncio
async def test_extract_keywords_repair_path_then_success(sample_spec):
    """First LLM reply is malformed JSON → Repair retries → second reply valid."""
    invalid = "{not json"
    valid = json.dumps({
        "keywords": ["周报", "团队报告", "weekly report", "progress summary"],
    })
    llm = FakeLLM([invalid, valid])
    out = await extract_intent_keywords(llm, model="haiku", spec=sample_spec)
    assert "周报" in out
    assert len(llm.calls) == 2
