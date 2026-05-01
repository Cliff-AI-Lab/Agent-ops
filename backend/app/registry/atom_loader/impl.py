"""AtomLoader implementation: YAML -> AtomDef via Pydantic validation."""

from __future__ import annotations

from pathlib import Path

import yaml

from app.registry.atom_loader.models import AtomDef


class AtomLoaderImpl:
    def load_one(self, yaml_path: Path) -> AtomDef:
        text = yaml_path.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        return AtomDef.model_validate(data)

    def load_all(self, base_dir: Path) -> dict[str, AtomDef]:
        atoms: dict[str, AtomDef] = {}
        for yaml_path in sorted(base_dir.rglob("*.yaml")):
            atom = self.load_one(yaml_path)
            if atom.asset_id in atoms:
                raise ValueError(
                    f"Duplicate asset_id {atom.asset_id!r}: "
                    f"{atoms[atom.asset_id].asset_id} vs {yaml_path}"
                )
            atoms[atom.asset_id] = atom
        return atoms
