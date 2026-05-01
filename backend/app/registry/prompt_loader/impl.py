"""PromptLoader implementation: YAML -> PromptDef + Jinja2 rendering."""

from __future__ import annotations

from pathlib import Path

import yaml
from jinja2 import StrictUndefined, Template

from app.registry.prompt_loader.models import PromptDef


class PromptLoaderImpl:
    def load_one(self, yaml_path: Path) -> PromptDef:
        text = yaml_path.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        return PromptDef.model_validate(data)

    def load_all(self, base_dir: Path) -> dict[str, PromptDef]:
        prompts: dict[str, PromptDef] = {}
        for yaml_path in sorted(base_dir.rglob("*.yaml")):
            p = self.load_one(yaml_path)
            if p.asset_id in prompts:
                raise ValueError(
                    f"Duplicate prompt asset_id {p.asset_id!r}: {yaml_path}"
                )
            prompts[p.asset_id] = p
        return prompts

    def render(self, prompt: PromptDef, vars: dict) -> str:
        # Validate required inputs are provided
        missing = [
            inp.name for inp in prompt.inputs if inp.required and inp.name not in vars
        ]
        if missing:
            raise ValueError(
                f"PromptLoader.render({prompt.asset_id}): missing required inputs: {missing}"
            )
        # StrictUndefined: undefined vars become explicit errors not silent ''
        template = Template(prompt.template, undefined=StrictUndefined)
        return template.render(**vars)
