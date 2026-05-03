"""Tests for DesignerRegistry."""
from __future__ import annotations

import pytest

from app.core.pipelines.factory.industry_designer import (
    DesignerRegistry,
    GeneralDesigner,
    default_registry,
)


class _FakeDesigner:
    def __init__(self, code: str) -> None:
        self.industry_code = code

    async def design_multi_agent(self, nl, classification):  # pragma: no cover
        raise NotImplementedError


def test_register_and_get_exact_match():
    reg = DesignerRegistry()
    finance = _FakeDesigner("02")
    reg.register(finance)
    assert reg.get("02") is finance


def test_get_falls_back_to_default():
    reg = DesignerRegistry(default_industry_code="01")
    general = _FakeDesigner("01")
    reg.register(general)
    # ask for 03 (not registered) -> fallback
    assert reg.get("03") is general


def test_get_raises_when_no_default_and_no_match():
    reg = DesignerRegistry(default_industry_code="01")
    reg.register(_FakeDesigner("02"))
    with pytest.raises(KeyError, match="default"):
        reg.get("99")


def test_register_replaces_existing():
    reg = DesignerRegistry()
    a = _FakeDesigner("01")
    b = _FakeDesigner("01")
    reg.register(a)
    reg.register(b)
    assert reg.get("01") is b


def test_codes_sorted():
    reg = DesignerRegistry()
    for code in ["02", "01", "06", "03"]:
        reg.register(_FakeDesigner(code))
    assert reg.codes() == ["01", "02", "03", "06"]


def test_contains():
    reg = DesignerRegistry()
    reg.register(_FakeDesigner("01"))
    assert "01" in reg
    assert "99" not in reg


def test_default_registry_includes_general():
    reg = default_registry()
    assert "01" in reg
    assert isinstance(reg.get("01"), GeneralDesigner)


def test_default_registry_falls_back_for_unknown_industry():
    reg = default_registry()
    # Industry 06 (医疗) not yet shipped -> falls back to GeneralDesigner
    designer = reg.get("06")
    assert isinstance(designer, GeneralDesigner)
