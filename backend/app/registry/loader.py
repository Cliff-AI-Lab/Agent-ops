"""YAML → CapabilityContract loader with Ontology/pipeline schema ref resolver."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from app.ontology import (
    Artifact,
    CapabilityContract,
    CodeArtifact,
    RequirementSpec,
    RunEvent,
    TaskRun,
    UIBlueprint,
)
from app.ontology.objects.code_graph import CodeGraph

_LOG = logging.getLogger("agent_ops.registry")

_ONTOLOGY_MAP: dict[str, type[BaseModel]] = {
    "RequirementSpec": RequirementSpec,
    "UIBlueprint": UIBlueprint,
    "CodeArtifact": CodeArtifact,
    "Artifact": Artifact,
    "RunEvent": RunEvent,
    "TaskRun": TaskRun,
    "CodeGraph": CodeGraph,
}

_PIPELINE_MAP: dict[str, type[BaseModel]] = {}


def _build_pipeline_map() -> dict[str, type[BaseModel]]:
    """Lazy-init pipeline schema map to avoid import cycles."""
    if _PIPELINE_MAP:
        return _PIPELINE_MAP
    from app.core.pipelines.ui.phase2_prototype import Phase2Output, PrototypeVariant

    class Phase3Input(BaseModel):
        """Synthesized schema for Phase3 input (blueprint + selected prototype)."""

        blueprint: UIBlueprint
        chosen_variant: PrototypeVariant

    _PIPELINE_MAP.update(
        Phase2Output=Phase2Output,
        PrototypeVariant=PrototypeVariant,
        Phase3Input=Phase3Input,
    )
    return _PIPELINE_MAP


def resolve_schema_ref(ref: str) -> dict[str, Any]:
    """Resolve a schema reference like ``ontology:RequirementSpec`` to JSON Schema.

    Returns a ``{"$comment": "unresolved ref: ..."}`` placeholder when the
    target is not found, so registry bootstrap never crashes on missing refs.
    """
    if ":" not in ref:
        return {"$comment": f"malformed ref: {ref}"}
    namespace, name = ref.split(":", 1)
    if namespace == "ontology":
        cls = _ONTOLOGY_MAP.get(name)
    elif namespace == "pipeline":
        cls = _build_pipeline_map().get(name)
    else:
        return {"$comment": f"unknown namespace: {namespace}"}
    if cls is None:
        return {"$comment": f"unresolved ref: {ref}"}
    return cls.model_json_schema()


def load_capability_from_yaml(path: Path) -> CapabilityContract:
    """Parse one YAML file into a CapabilityContract."""
    with path.open(encoding="utf-8") as fp:
        data = yaml.safe_load(fp) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path.name}: top-level YAML must be a mapping")
    # Resolve *_schema_ref into *_schema (extra=forbid on CapabilityContract)
    if "input_schema_ref" in data and "input_schema" not in data:
        data["input_schema"] = resolve_schema_ref(data.pop("input_schema_ref"))
    if "output_schema_ref" in data and "output_schema" not in data:
        data["output_schema"] = resolve_schema_ref(data.pop("output_schema_ref"))
    data.pop("input_schema_ref", None)
    data.pop("output_schema_ref", None)
    return CapabilityContract.model_validate(data)


def load_capabilities_from_dir(dir_path: Path) -> list[CapabilityContract]:
    """Load every ``*.yaml`` under ``dir_path``. Bad files are logged, not raised."""
    if not dir_path.is_dir():
        _LOG.warning("capabilities dir not found: %s", dir_path)
        return []
    contracts: list[CapabilityContract] = []
    for yaml_path in sorted(dir_path.glob("*.yaml")):
        try:
            contracts.append(load_capability_from_yaml(yaml_path))
        except Exception as exc:  # noqa: BLE001 — protect startup
            _LOG.warning("failed to load %s: %s", yaml_path.name, exc)
    return contracts
