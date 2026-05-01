"""Tests for DSLValidatorImpl V1."""

from __future__ import annotations

import pytest
import yaml

from app.core.pipelines.factory.validator import DSLValidator, DSLValidatorImpl
from app.core.pipelines.factory.validator.interface import (
    ValidationIssue,
    ValidationReport,
)


def test_interface_importable():
    assert DSLValidator is not None


def test_validation_report_constructs():
    rpt = ValidationReport(ok=True, target="dify")
    assert rpt.ok is True
    assert rpt.target == "dify"
    assert rpt.issues == []


def test_validation_issue_constructs():
    issue = ValidationIssue(severity="error", message="missing field")
    assert issue.severity == "error"


@pytest.mark.asyncio
async def test_invalid_target_rejected():
    v = DSLValidatorImpl()
    report = await v.validate("...", "unknown_target")
    assert report.ok is False
    assert any("unsupported target" in i.message for i in report.issues)


@pytest.mark.asyncio
async def test_yaml_parse_error_caught():
    v = DSLValidatorImpl()
    report = await v.validate(":\n  - invalid: yaml: structure: here", "dify")
    assert report.ok is False
    assert any("YAML parse" in i.message for i in report.issues)


@pytest.mark.asyncio
async def test_missing_app_key_rejected():
    v = DSLValidatorImpl()
    report = await v.validate("workflow:\n  graph:\n    nodes: []\n", "dify")
    assert report.ok is False
    assert any("'app'" in i.message for i in report.issues)


@pytest.mark.asyncio
async def test_happy_dify_yaml_passes():
    dsl = yaml.safe_dump(
        {
            "app": {"name": "x", "mode": "workflow"},
            "workflow": {
                "graph": {
                    "nodes": [
                        {"id": "s1", "type": "code", "data": {}},
                        {"id": "s2", "type": "llm", "data": {}},
                    ],
                    "edges": [{"id": "e1", "source": "s1", "target": "s2"}],
                }
            },
        },
        allow_unicode=True,
    )
    v = DSLValidatorImpl()
    report = await v.validate(dsl, "dify")
    assert report.ok is True
    error_issues = [i for i in report.issues if i.severity == "error"]
    assert error_issues == []


@pytest.mark.asyncio
async def test_dangling_edge_rejected():
    dsl = yaml.safe_dump(
        {
            "app": {"name": "x"},
            "workflow": {
                "graph": {
                    "nodes": [{"id": "s1", "type": "code", "data": {}}],
                    "edges": [{"source": "s1", "target": "ghost"}],
                }
            },
        },
        allow_unicode=True,
    )
    v = DSLValidatorImpl()
    report = await v.validate(dsl, "dify")
    assert report.ok is False
    assert any("'ghost'" in i.message for i in report.issues)


@pytest.mark.asyncio
async def test_node_missing_type_rejected():
    dsl = yaml.safe_dump(
        {
            "app": {"name": "x"},
            "workflow": {
                "graph": {
                    "nodes": [{"id": "s1", "data": {}}],
                    "edges": [],
                }
            },
        },
        allow_unicode=True,
    )
    v = DSLValidatorImpl()
    report = await v.validate(dsl, "dify")
    assert report.ok is False
    assert any("missing type" in i.message for i in report.issues)


@pytest.mark.asyncio
async def test_ruidong_placeholder_emits_info_not_error():
    dsl = yaml.safe_dump(
        {
            "app": {"name": "x"},
            "workflow": {
                "graph": {
                    "nodes": [
                        {
                            "id": "s1",
                            "type": "llm",
                            "data": {"model": "${RUIDONG_MODEL_FOR_TEST_M}"},
                        }
                    ],
                    "edges": [],
                }
            },
        },
        allow_unicode=True,
    )
    v = DSLValidatorImpl()
    report = await v.validate(dsl, "dify")
    assert report.ok is True
    info_issues = [i for i in report.issues if i.severity == "info"]
    assert any("RUIDONG_MODEL_FOR_TEST_M" in i.message for i in info_issues)
