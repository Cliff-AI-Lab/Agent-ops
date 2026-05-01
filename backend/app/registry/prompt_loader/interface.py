"""PromptLoader Protocol."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from app.registry.prompt_loader.models import PromptDef


class PromptLoader(Protocol):
    """Load prompt YAML files into PromptDef Pydantic models."""

    def load_one(self, yaml_path: Path) -> PromptDef:
        """Load a single YAML file into a PromptDef."""
        ...

    def load_all(self, base_dir: Path) -> dict[str, PromptDef]:
        """Recursively load all *.yaml under base_dir. Return {asset_id: PromptDef}."""
        ...

    def render(self, prompt: PromptDef, vars: dict) -> str:
        """Substitute Jinja2 variables in template. Validates required inputs."""
        ...
