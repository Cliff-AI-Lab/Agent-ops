"""Skeleton tests for HealthChecker."""

import pytest

from app.registry.health_checker import HealthChecker, HealthCheckerImpl


def test_interface_importable():
    assert HealthChecker is not None


def test_impl_constructable():
    hc = HealthCheckerImpl()
    assert hc is not None


@pytest.mark.asyncio
async def test_check_one_stub_raises():
    hc = HealthCheckerImpl()
    with pytest.raises(NotImplementedError):
        await hc.check_one("atom.db.postgres.v1")
