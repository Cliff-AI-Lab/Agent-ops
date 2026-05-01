"""AgentInputCoercer tests (Batch I-2)."""
from __future__ import annotations

import json

import pytest

from app.core.dialog.agent_input_coercer import (
    coerce_inputs,
    heuristic_coerce,
    missing_required,
    required_keys,
    schema_properties,
)
from app.core.llm.client import ChatMessage


# ---------- Schema helpers ----------
def test_required_keys_handles_missing():
    assert required_keys(None) == []
    assert required_keys({}) == []
    assert required_keys({"required": ["a", "b"]}) == ["a", "b"]


def test_schema_properties_handles_missing():
    assert schema_properties(None) == {}
    assert schema_properties({"properties": {"x": {"type": "string"}}}) == {"x": {"type": "string"}}


def test_missing_required_detects_absent():
    schema = {"required": ["a", "b"], "properties": {}}
    assert missing_required({}, schema) == ["a", "b"]
    assert missing_required({"a": 1}, schema) == ["b"]
    assert missing_required({"a": 1, "b": 2}, schema) == []


def test_missing_required_treats_none_as_missing():
    schema = {"required": ["a"]}
    assert missing_required({"a": None}, schema) == ["a"]


# ---------- Heuristic ----------
def test_heuristic_picks_canonical_message_key():
    schema = {
        "type": "object",
        "properties": {
            "message": {"type": "string"},
            "extra": {"type": "string"},
        },
        "required": ["message"],
    }
    out = heuristic_coerce("hello there", schema)
    assert out == {"message": "hello there"}


def test_heuristic_uses_single_string_key_when_no_canonical_match():
    schema = {
        "type": "object",
        "properties": {"some_arbitrary_text": {"type": "string"}},
    }
    out = heuristic_coerce("hi", schema)
    assert out == {"some_arbitrary_text": "hi"}


def test_heuristic_returns_none_when_no_string_keys():
    schema = {"properties": {"count": {"type": "integer"}}}
    assert heuristic_coerce("hi", schema) is None


def test_heuristic_returns_none_for_empty_schema():
    assert heuristic_coerce("hi", {}) is None
    assert heuristic_coerce("hi", None) is None


def test_heuristic_fills_extra_required_with_empty_strings():
    schema = {
        "properties": {
            "query": {"type": "string"},
            "topic": {"type": "string"},
        },
        "required": ["query", "topic"],
    }
    out = heuristic_coerce("python web scraping", schema)
    assert out == {"query": "python web scraping", "topic": ""}


# ---------- coerce_inputs orchestration ----------
class FakeLLM:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls = 0

    async def chat(self, model, messages, *, temperature=0.0, max_tokens=None, tools=None):
        self.calls += 1
        if not self._responses:
            raise RuntimeError("FakeLLM out of canned responses")
        return ChatMessage(role="assistant", content=self._responses.pop(0))


@pytest.mark.asyncio
async def test_coerce_empty_schema_returns_message_default():
    out = await coerce_inputs("hi", None)
    assert out.method == "empty_schema"
    assert out.inputs == {"message": "hi"}
    assert out.missing_required == []


@pytest.mark.asyncio
async def test_coerce_named_string_key_uses_heuristic():
    schema = {"properties": {"query": {"type": "string"}}, "required": ["query"]}
    out = await coerce_inputs("python tips", schema)
    assert out.method == "heuristic"
    assert out.inputs == {"query": "python tips"}
    assert out.missing_required == []


@pytest.mark.asyncio
async def test_coerce_complex_schema_calls_llm():
    schema = {
        "type": "object",
        "properties": {
            "topic": {"type": "string"},
            "count": {"type": "integer"},
            "include_examples": {"type": "boolean"},
        },
        "required": ["topic", "count"],
    }
    payload = {"payload": {"topic": "python web", "count": 5, "include_examples": True}}
    llm = FakeLLM([json.dumps(payload)])
    out = await coerce_inputs(
        "找 5 个 python 网络爬虫的例子",
        schema, llm=llm, model="haiku",
    )
    assert out.method == "llm"
    assert out.inputs["topic"] == "python web"
    assert out.inputs["count"] == 5
    assert out.missing_required == []
    assert llm.calls == 1


@pytest.mark.asyncio
async def test_coerce_llm_unparseable_falls_back():
    schema = {
        "type": "object",
        "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
        "required": ["a", "b"],
    }
    llm = FakeLLM(["not valid json at all"])
    out = await coerce_inputs("just words", schema, llm=llm, model="haiku")
    assert out.method == "fallback"
    # Final fallback retains the message + stubs required keys
    assert out.inputs["message"] == "just words"
    assert "a" in out.inputs and "b" in out.inputs
    # missing_required only flags absent / None — empty-string stubs satisfy presence
    assert out.missing_required == []
    assert llm.calls == 1


@pytest.mark.asyncio
async def test_coerce_llm_partial_payload_keeps_what_it_got():
    schema = {
        "type": "object",
        "properties": {
            "topic": {"type": "string"},
            "lang": {"type": "string"},
        },
        "required": ["topic", "lang"],
    }
    # LLM only filled topic, missed lang — coercer keeps payload but flags missing
    payload = {"payload": {"topic": "test"}}
    llm = FakeLLM([json.dumps(payload)])
    out = await coerce_inputs("test", schema, llm=llm, model="haiku")
    assert out.method == "llm"
    assert out.inputs["topic"] == "test"
    assert out.missing_required == ["lang"]


@pytest.mark.asyncio
async def test_coerce_llm_dropped_payload_wrapper_still_accepted():
    """Some models ignore the {payload: ...} wrapper and emit the inner dict directly."""
    # Multi-string-key, non-canonical names → heuristic gives up, LLM path triggers
    schema = {
        "type": "object",
        "properties": {
            "topic_a": {"type": "string"},
            "topic_b": {"type": "string"},
        },
        "required": ["topic_a", "topic_b"],
    }
    llm = FakeLLM([json.dumps({"topic_a": "x", "topic_b": "y"})])
    out = await coerce_inputs("hi", schema, llm=llm, model="haiku")
    assert out.method == "llm"
    assert out.inputs == {"topic_a": "x", "topic_b": "y"}


@pytest.mark.asyncio
async def test_coerce_no_llm_provided_falls_back():
    """When schema is complex and no LLM passed, we land on fallback."""
    schema = {
        "type": "object",
        "properties": {"a": {"type": "integer"}},
        "required": ["a"],
    }
    out = await coerce_inputs("hi", schema)  # no llm/model
    assert out.method == "fallback"
    assert out.inputs == {"message": "hi", "a": ""}
