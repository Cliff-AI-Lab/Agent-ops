"""Skeleton tests for IntentParser component.

Phase 1 W2 will add real LLM integration tests.
For now: contract tests (interface importable, stub raises correctly).
"""

import pytest

from app.core.pipelines.factory.intent_parser import IntentParser, IntentParserImpl


def test_interface_importable():
    """IntentParser Protocol should be importable."""
    assert IntentParser is not None


def test_impl_importable():
    """IntentParserImpl should be importable."""
    assert IntentParserImpl is not None


def test_impl_can_be_constructed():
    """IntentParserImpl should accept None llm_client (for tests)."""
    parser = IntentParserImpl(llm_client=None)
    assert parser is not None


@pytest.mark.asyncio
async def test_impl_parse_raises_not_implemented():
    """Phase 1 W2 stub: parse() should raise NotImplementedError."""
    parser = IntentParserImpl(llm_client=None)
    with pytest.raises(NotImplementedError):
        await parser.parse("test")
