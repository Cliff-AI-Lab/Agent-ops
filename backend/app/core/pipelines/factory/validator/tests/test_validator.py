"""Skeleton tests for Validator component."""

import pytest

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
async def test_validate_stub_raises():
    v = DSLValidatorImpl()
    with pytest.raises(NotImplementedError):
        await v.validate("...", "dify")
