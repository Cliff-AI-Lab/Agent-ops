"""Skeleton tests for Resolver component."""

import pytest

from app.core.pipelines.factory.resolver import Resolver, ResolverImpl


def test_interface_importable():
    assert Resolver is not None


def test_impl_constructable():
    r = ResolverImpl()
    assert r is not None


@pytest.mark.asyncio
async def test_impl_resolve_stub_raises():
    from app.core.pipelines.factory.ir import (
        StructuredIntent,
        TriggerSpec,
    )

    intent = StructuredIntent(
        goal="test",
        trigger=TriggerSpec(type="manual"),
        steps=[],
        outputs=[],
        raw_user_input="test",
    )
    r = ResolverImpl()
    with pytest.raises(NotImplementedError):
        await r.resolve(intent)
