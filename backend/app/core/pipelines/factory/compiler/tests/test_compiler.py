"""Skeleton tests for DSLCompiler component."""

import pytest

from app.core.pipelines.factory.compiler import DSLCompiler, DifyCompilerImpl
from app.core.pipelines.factory.ir import ResolvedDAG


def test_interface_importable():
    assert DSLCompiler is not None


def test_impl_constructable():
    c = DifyCompilerImpl()
    assert c is not None


def test_impl_rejects_n8n_target():
    """DifyCompilerImpl should raise ValueError for n8n target."""
    c = DifyCompilerImpl()
    dag = ResolvedDAG(intent_ref="test", nodes=[], edges=[], target="n8n")
    with pytest.raises(ValueError, match="dify/hybrid"):
        c.compile(dag)


def test_impl_compile_stub_raises():
    c = DifyCompilerImpl()
    dag = ResolvedDAG(intent_ref="test", nodes=[], edges=[], target="dify")
    with pytest.raises(NotImplementedError):
        c.compile(dag)
