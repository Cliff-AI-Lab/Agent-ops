"""Skeleton tests for SearchEngine."""

import pytest

from app.registry.search_engine import SearchEngine, SearchEngineImpl


def test_interface_importable():
    assert SearchEngine is not None


def test_impl_constructable():
    se = SearchEngineImpl()
    assert se is not None


def test_search_stub_raises():
    se = SearchEngineImpl()
    with pytest.raises(NotImplementedError):
        se.search_atoms("test")
