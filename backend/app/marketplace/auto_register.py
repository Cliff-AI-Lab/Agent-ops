"""Auto-register a freshly delivered artifact as a GeneratedAsset (Batch X).

Wired into ``DeliverPipeline`` after PresetInjector + Packager. Steps:

    1. Compute zip on disk → ``assets/{asset_id}/project.zip``
    2. Synthesize an AgentContract YAML at
       ``agents/__generated__/{asset_id}/agent.yaml`` so the IntentRouter can
       pick it up after a registry reload.
    3. INSERT a row into the ``generated_assets`` SQLite table (status='draft').

Result: every successful run is auditable, reproducible, and one promotion
away from being a first-class member of the RegistryHub.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime
from pathlib import Path

import yaml

from app.core.trace.bus import emit
from app.delivery.packager import package_as_zip
from app.marketplace.asset_store import (
    default_assets_root,
    default_generated_agents_dir,
    get_asset_store,
)
from app.ontology import (
    AgentContract,
    AgentGenerationMetadata,
    CodeArtifact,
    GeneratedAsset,
    RequirementSpec,
)

_LOG = logging.getLogger("agent_ops.marketplace.auto_register")

_SLUG_RE = re.compile(r"[^a-z0-9-]+")


def _slug(text: str) -> str:
    out = _SLUG_RE.sub("-", text.lower()).strip("-")
    return out or "asset"


def _make_asset_id(spec: RequirementSpec | None, run_id: str | None) -> str:
    base = run_id or uuid.uuid4().hex[:12]
    if spec and spec.product_name:
        return f"{_slug(spec.product_name)}-{base[:8]}"
    return base


def _agent_contract_for(
    asset_id: str,
    spec: RequirementSpec | None,
    artifact: CodeArtifact,
    *,
    source_session_id: str | None,
    source_sop: str | None,
    intent_keywords: list[str] | None = None,
) -> AgentContract:
    """Synthesize an AgentContract describing the just-delivered product."""
    name = (spec.product_name if spec else asset_id) or asset_id
    description = (
        f"用户通过 Agent Ops 生成的{spec.product_type if spec else 'app'} 产物 ({asset_id})。\n"
        f"原始 SOP: {source_sop or '(未记录)'}"
    )
    if intent_keywords is None:
        # Best-effort fallback: combine product name + special_requirements
        intent_keywords = []
        if spec:
            intent_keywords = [spec.product_name] + list(spec.special_requirements or [])
    return AgentContract(
        agent_id=f"gen.{asset_id}",
        version="0.1.0",
        name=name,
        description=description,
        owner="agent-ops-platform",
        intent_keywords=intent_keywords,
        tags=["generated", spec.product_type if spec else "app"],
        is_generated=True,
        generation=AgentGenerationMetadata(
            generation_id=asset_id,
            source_session_id=source_session_id,
            source_sop=source_sop,
            generated_at=datetime.utcnow(),
        ),
        activation_status="draft",
    )


def auto_register(
    artifact: CodeArtifact,
    spec: RequirementSpec | None = None,
    *,
    run_id: str | None = None,
    source_session_id: str | None = None,
    source_sop: str | None = None,
    assets_root: Path | None = None,
    generated_agents_root: Path | None = None,
    intent_keywords: list[str] | None = None,
) -> GeneratedAsset:
    """Persist the artifact + register a draft GeneratedAsset row.

    Idempotent on ``asset_id``: re-running with the same id replaces the row
    and overwrites the zip / yaml.
    """
    asset_id = _make_asset_id(spec, run_id)
    assets_root = assets_root or default_assets_root()
    gen_agents_root = generated_agents_root or default_generated_agents_dir()

    # 1) Write zip to disk
    zip_dir = assets_root / asset_id
    zip_dir.mkdir(parents=True, exist_ok=True)
    zip_bytes = package_as_zip(artifact, project_name=_slug(spec.product_name if spec else asset_id))
    zip_path = zip_dir / "project.zip"
    zip_path.write_bytes(zip_bytes)

    # 2) Write auto-synthesized agent.yaml
    agent_dir = gen_agents_root / asset_id
    agent_dir.mkdir(parents=True, exist_ok=True)
    contract = _agent_contract_for(
        asset_id, spec, artifact,
        source_session_id=source_session_id,
        source_sop=source_sop,
        intent_keywords=intent_keywords,
    )
    yaml_path = agent_dir / "agent.yaml"
    yaml_path.write_text(
        yaml.safe_dump(contract.model_dump(mode="json"), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    # 3) Build the GeneratedAsset row
    asset = GeneratedAsset(
        asset_id=asset_id,
        name=contract.name,
        description=contract.description,
        product_type=spec.product_type if spec else "app",
        source_session_id=source_session_id,
        source_sop=source_sop,
        generation_id=asset_id,
        generated_at=datetime.utcnow(),
        artifact_path=str(zip_path.relative_to(assets_root.parent)).replace("\\", "/"),
        agent_yaml_path=str(yaml_path.relative_to(assets_root.parent)).replace("\\", "/"),
        file_count=len(artifact.files),
        total_bytes=len(zip_bytes),
        intent_keywords=contract.intent_keywords,
        tags=contract.tags,
        capabilities_used=list(contract.capabilities_used),
        tools_used=list(contract.tools_used),
        status="draft",
    )

    get_asset_store().register(asset)
    emit(
        "L6", "Marketplace", "asset_registered",
        f"asset_id={asset_id} · status=draft · files={asset.file_count} · bytes={asset.total_bytes}",
        data={
            "asset_id": asset_id,
            "artifact_path": asset.artifact_path,
            "agent_yaml_path": asset.agent_yaml_path,
            "is_generated": True,
        },
    )
    return asset


def reload_generated_agents() -> int:
    """Scan ``agents/__generated__/`` and re-register active assets into the live AgentStore.

    Promoting an asset (status='active') causes a subsequent reload to make
    it visible to the IntentRouter shortlist.
    """
    from app.registry.agent_store import (
        get_agent_store,
        load_agents_from_dir,
    )

    root = default_generated_agents_dir()
    if not root.is_dir():
        return 0
    pairs = load_agents_from_dir(root)
    # Only register the ones whose backing asset is 'active'
    store = get_asset_store()
    active_ids = {a.asset_id for a in store.list(status="active")}
    keep: list[tuple] = []
    for agent, path in pairs:
        # Auto-generated agent_id format is 'gen.{asset_id}'
        if agent.agent_id.startswith("gen.") and agent.agent_id[4:] in active_ids:
            # AssetStore.promote() is the source of truth for activation;
            # override the YAML-declared status so RegistryHub's
            # `activation_status='active'` filter sees this agent.
            active_agent = agent.model_copy(update={"activation_status": "active"})
            keep.append((active_agent, path))
    n = get_agent_store().register_many(keep)
    emit(
        "L6", "Marketplace", "registry_reload",
        f"reloaded {n} promoted assets into AgentStore",
        data={"promoted_count": n},
    )
    return n
