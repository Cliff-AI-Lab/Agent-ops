"""EvalSet loader: YAML -> EvalSet Pydantic model."""

from __future__ import annotations

from pathlib import Path

import yaml

from app.registry.eval_runner.models import EvalSet


def load_eval_set(yaml_path: Path) -> EvalSet:
    """Load a single eval set YAML."""
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    return EvalSet.model_validate(data)


def load_eval_sets(base_dir: Path) -> dict[str, EvalSet]:
    """Recursively load all *.yaml under base_dir.

    Returns {asset_id: EvalSet}.
    """
    out: dict[str, EvalSet] = {}
    for yaml_path in sorted(base_dir.rglob("*.yaml")):
        es = load_eval_set(yaml_path)
        if es.asset_id in out:
            raise ValueError(
                f"Duplicate eval_set asset_id {es.asset_id}: {yaml_path}"
            )
        out[es.asset_id] = es
    return out
