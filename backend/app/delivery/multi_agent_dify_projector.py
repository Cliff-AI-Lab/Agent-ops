"""MultiAgentDifyProjector - Phase 10 W3 D1 per-specialist Dify projection.

Multi-agent systems compile to Python (OpenAI Agents SDK) by default. For
operators who want to inspect / hand-tune individual specialists in Dify
Studio, this projector pushes each specialist as its own Dify workflow:

    MultiAgentSpec
        |
        +- triage           (kept Python-only in MVP; W3 D2 will project)
        +- specialist_1 ──► FactoryPipeline.build(nl_brief) ──► Dify YAML
        |                ──► DifyPublisher.publish ──► dify_app_id_1
        +- specialist_2 ──► ... ──► dify_app_id_2
        +- specialist_N ──► ... ──► dify_app_id_N

The mapping {specialist_id → dify_app_id} is persisted to
``.factory_deploy_log/<system_slug>.multi-agent.json`` so subsequent
operations (drift-check, rebuild, AgentDependencyAnalyzer surface) can
reach the corresponding Dify app.

W3 D1 scope (this commit):
  - per-specialist build + publish, idempotent re-projection reuses same
    dify_app_id (mirrors DifyPublisher single-spec behavior)
  - mapping JSON written under deploy_log
  - returns ProjectionResult with per-specialist status

W3 D2 (next):
  - handoff webhook bridging (Dify HTTP node) so the specialists can
    cross-call at runtime
  - cross-specialist drift detection
  - triage as a Dify workflow too (currently triage stays in the Python
    runtime)

R3 reuse audit: FactoryPipeline (single-agent), DifyPublisher (publish +
deploy_log), MultiAgentSpec (Phase 7 IR) are all unchanged. Everything
new lives in this module.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.core.pipelines.factory.ir.multi_agent import MultiAgentSpec
    from app.delivery.dify_publisher import DifyPublisher

logger = logging.getLogger(__name__)


@dataclass
class SpecialistProjection:
    """Per-specialist outcome of a project() call."""

    specialist_id: str
    specimen_id: str
    dify_app_id: str
    status: str
    yaml_chars: int = 0
    is_first_deploy: bool = False
    deploy_count: int = 0
    error: str = ""


@dataclass
class ProjectionResult:
    """Aggregate outcome of projecting one multi-agent spec."""

    system_slug: str
    projected_at: str
    triage_projected: bool = False
    specialists: list[SpecialistProjection] = field(default_factory=list)
    mapping_path: Path | None = None

    @property
    def success_count(self) -> int:
        return sum(1 for s in self.specialists if not s.error)

    @property
    def fail_count(self) -> int:
        return sum(1 for s in self.specialists if s.error)

    def short_summary(self) -> str:
        return (
            f"projected system={self.system_slug} "
            f"specialists={self.success_count}/{len(self.specialists)} ok"
            + (f" ({self.fail_count} failed)" if self.fail_count else "")
        )


@dataclass
class _MappingFile:
    """JSON shape for <system_slug>.multi-agent.json."""

    system_slug: str
    updated_at: str
    triage_dify_app_id: str | None
    specialists: dict[str, str]  # specialist_id -> dify_app_id

    def to_json(self) -> str:
        return json.dumps(
            {
                "system_slug": self.system_slug,
                "updated_at": self.updated_at,
                "triage_dify_app_id": self.triage_dify_app_id,
                "specialists": self.specialists,
            },
            ensure_ascii=False,
            indent=2,
        )


class MultiAgentDifyProjector:
    """Pushes a multi-agent spec as N Dify workflows (one per specialist).

    Args:
        publisher: DifyPublisher used to publish each specialist.
        build_fn: async callable (nl: str) -> dict with key "outputs.dify"
                  matching FactoryPipeline.build result shape. Tests can
                  pass a stub; production passes _run_build.
        log_dir: where to write the system mapping JSON
                 (default: <harness>/.factory_deploy_log).
    """

    def __init__(
        self,
        publisher: "DifyPublisher",
        build_fn,
        log_dir: Path | str | None = None,
    ):
        self.publisher = publisher
        self._build = build_fn
        self.log_dir = Path(log_dir) if log_dir else self._default_log_dir()
        self.log_dir.mkdir(parents=True, exist_ok=True)

    async def project(
        self,
        spec: "MultiAgentSpec",
        system_slug: str | None = None,
    ) -> ProjectionResult:
        slug = system_slug or self._slugify(spec.name)
        existing = self._read_mapping(slug)
        prior_specialists = (existing.specialists if existing else {})

        result = ProjectionResult(
            system_slug=slug,
            projected_at=_utc_now(),
        )

        new_mapping: dict[str, str] = dict(prior_specialists)

        for sp in spec.specialists:
            specimen_id = f"{slug}.{sp.id}"
            outcome = await self._project_one(specimen_id, sp.nl_brief, sp.name)
            result.specialists.append(
                SpecialistProjection(
                    specialist_id=sp.id,
                    specimen_id=specimen_id,
                    dify_app_id=outcome.get("dify_app_id", ""),
                    status=outcome.get("status", "unknown"),
                    yaml_chars=outcome.get("yaml_chars", 0),
                    is_first_deploy=outcome.get("is_first_deploy", False),
                    deploy_count=outcome.get("deploy_count", 0),
                    error=outcome.get("error", ""),
                )
            )
            if outcome.get("dify_app_id"):
                new_mapping[sp.id] = outcome["dify_app_id"]

        mapping = _MappingFile(
            system_slug=slug,
            updated_at=result.projected_at,
            triage_dify_app_id=(
                existing.triage_dify_app_id if existing else None
            ),
            specialists=new_mapping,
        )
        result.mapping_path = self._write_mapping(mapping)
        return result

    # ----- internals -----------------------------------------------------

    async def _project_one(
        self, specimen_id: str, nl: str, display_name: str
    ) -> dict[str, Any]:
        try:
            build_result = await self._build(nl)
        except Exception as exc:  # noqa: BLE001
            logger.warning("specialist build failed for %s: %s", specimen_id, exc)
            return {"error": f"build failed: {exc}"}

        outputs = build_result.get("outputs", {})
        yaml_text = outputs.get("dify") or build_result.get("dsl") or ""
        if not yaml_text:
            return {"error": "build produced no Dify YAML"}

        try:
            res = self.publisher.publish(specimen_id, yaml_text, app_name=display_name)
        except RuntimeError as exc:  # noqa: BLE001
            logger.warning("specialist publish failed for %s: %s", specimen_id, exc)
            return {"error": f"publish failed: {exc}", "yaml_chars": len(yaml_text)}

        return {
            "dify_app_id": res.dify_app_id,
            "status": res.status,
            "is_first_deploy": res.is_first_deploy,
            "deploy_count": res.deploy_count,
            "yaml_chars": len(yaml_text),
            "error": res.error,
        }

    def mapping_path(self, system_slug: str) -> Path:
        safe = self._slugify(system_slug)
        return self.log_dir / f"{safe}.multi-agent.json"

    def _read_mapping(self, system_slug: str) -> _MappingFile | None:
        p = self.mapping_path(system_slug)
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return _MappingFile(
                system_slug=data.get("system_slug", system_slug),
                updated_at=data.get("updated_at", ""),
                triage_dify_app_id=data.get("triage_dify_app_id"),
                specialists=data.get("specialists", {}),
            )
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning("mapping %s unreadable: %s", p, exc)
            return None

    def _write_mapping(self, mapping: _MappingFile) -> Path:
        p = self.mapping_path(mapping.system_slug)
        p.write_text(mapping.to_json(), encoding="utf-8")
        return p

    @staticmethod
    def _default_log_dir() -> Path:
        # backend/app/delivery/multi_agent_dify_projector.py
        # parents[3] -> harness root
        return Path(__file__).resolve().parents[3] / ".factory_deploy_log"

    @staticmethod
    def _slugify(name: str) -> str:
        out = []
        for ch in name:
            if ch.isalnum() or ch in "-_.":
                out.append(ch)
            elif ch in " /\\":
                out.append("_")
        return "".join(out).strip("._-") or "unnamed_system"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
