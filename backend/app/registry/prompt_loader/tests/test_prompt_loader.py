"""Tests for PromptLoader."""

from pathlib import Path

import pytest

from app.registry.prompt_loader import (
    PromptDef,
    PromptInput,
    PromptLoaderImpl,
    PromptOutputSchema,
    PromptProvenance,
    PromptTestCase,
)


def _make_prompt(**overrides) -> PromptDef:
    base = dict(
        schema_version="1.0",
        asset_id="prompt.test.basic.v1",
        version="1.0.0",
        name="测试 Prompt",
        description="A minimal prompt used for unit tests only.",
        tags=["test"],
        template="你好 {{name}}, 请帮我处理以下数据: {{data}}",
        inputs=[
            PromptInput(name="name", type="string"),
            PromptInput(name="data", type="json_object"),
        ],
        output=PromptOutputSchema(format="text"),
        test_cases=[PromptTestCase(name="happy", inputs={"name": "x", "data": "y"})],
        maintainer="tests / 2026-05",
        provenance=PromptProvenance(built_at="2026-05-01"),
    )
    base.update(overrides)
    return PromptDef(**base)


def test_loader_constructable():
    loader = PromptLoaderImpl()
    assert loader is not None


def test_invalid_asset_id_rejected():
    with pytest.raises(Exception):
        _make_prompt(asset_id="bad-format")


def test_short_description_rejected():
    with pytest.raises(Exception):
        _make_prompt(description="too short")


def test_short_template_rejected():
    with pytest.raises(Exception):
        _make_prompt(template="x")


def test_render_substitutes_variables():
    loader = PromptLoaderImpl()
    p = _make_prompt()
    out = loader.render(p, {"name": "张三", "data": {"x": 1}})
    assert "你好 张三" in out
    assert "{'x': 1}" in out


def test_render_rejects_missing_required():
    loader = PromptLoaderImpl()
    p = _make_prompt()
    with pytest.raises(ValueError, match="missing required inputs"):
        loader.render(p, {"name": "x"})  # missing 'data'


def test_render_rejects_undefined_in_template():
    """StrictUndefined: extra vars in template not provided should raise."""
    from jinja2 import UndefinedError

    loader = PromptLoaderImpl()
    p = _make_prompt(
        template="hello {{name}} and {{ghost_var}}",
        inputs=[PromptInput(name="name", type="string")],
    )
    with pytest.raises(UndefinedError):
        loader.render(p, {"name": "x"})


def test_load_seed_prompts_if_present():
    """If capabilities/prompts/ exists with seed prompts, all should validate."""
    repo_root = Path(__file__).resolve().parents[5]
    base = repo_root / "capabilities" / "prompts"
    if not base.exists() or not any(base.rglob("*.yaml")):
        pytest.skip(f"no seed prompts dir or empty: {base}")
    loader = PromptLoaderImpl()
    prompts = loader.load_all(base)
    assert len(prompts) >= 1
    for p in prompts.values():
        assert isinstance(p, PromptDef)
