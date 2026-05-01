"""AtomLoader Protocol."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from app.registry.atom_loader.models import AtomDef


class AtomLoader(Protocol):
    """Load atom YAML files into AtomDef Pydantic models."""

    def load_one(self, yaml_path: Path) -> AtomDef:
        """Load a single YAML file into an AtomDef."""
        ...

    def load_all(self, base_dir: Path) -> dict[str, AtomDef]:
        """Recursively load all *.yaml under base_dir. Return {asset_id: AtomDef}."""
        ...
