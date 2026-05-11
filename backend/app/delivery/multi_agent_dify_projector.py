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
class TriageProjection:
    """Triage outcome of a project() call (Phase 10 W3 D2)."""

    specimen_id: str
    dify_app_id: str
    status: str
    yaml_chars: int = 0
    is_first_deploy: bool = False
    deploy_count: int = 0
    error: str = ""


@dataclass
class HandoffEdgeMetadata:
    """One handoff edge from the spec mapped to its runtime targets.

    Phase 10 W3 D2 ships metadata only; the actual webhook node injection
    into Dify happens in W3 D3 so the user can review the topology first.
    """

    from_agent: str
    to_agent: str
    from_dify_app_id: str | None
    to_dify_app_id: str | None
    when: str = ""


@dataclass
class InjectedAgent:
    """Per-source-agent injection outcome (W3 D4)."""

    source_agent: str
    injected_targets: list[str] = field(default_factory=list)
    republish_status: str = ""
    republish_error: str = ""


@dataclass
class ProjectionResult:
    """Aggregate outcome of projecting one multi-agent spec."""

    system_slug: str
    projected_at: str
    triage: TriageProjection | None = None
    specialists: list[SpecialistProjection] = field(default_factory=list)
    handoff_edges: list[HandoffEdgeMetadata] = field(default_factory=list)
    injected_agents: list[InjectedAgent] = field(default_factory=list)
    mapping_path: Path | None = None

    @property
    def triage_projected(self) -> bool:
        """Backward-compat flag from W3 D1."""
        return self.triage is not None and not self.triage.error

    @property
    def success_count(self) -> int:
        return sum(1 for s in self.specialists if not s.error)

    @property
    def fail_count(self) -> int:
        return sum(1 for s in self.specialists if s.error)

    def short_summary(self) -> str:
        parts = [
            f"projected system={self.system_slug}",
            f"specialists={self.success_count}/{len(self.specialists)} ok",
        ]
        if self.fail_count:
            parts.append(f"({self.fail_count} failed)")
        if self.triage is not None:
            tri = "ok" if not self.triage.error else "FAIL"
            parts.append(f"triage={tri}")
        if self.handoff_edges:
            parts.append(f"handoffs={len(self.handoff_edges)}")
        return " ".join(parts)


@dataclass
class _MappingFile:
    """JSON shape for <system_slug>.multi-agent.json."""

    system_slug: str
    updated_at: str
    triage_dify_app_id: str | None
    specialists: dict[str, str]  # specialist_id -> dify_app_id
    handoff_edges: list[dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(
            {
                "system_slug": self.system_slug,
                "updated_at": self.updated_at,
                "triage_dify_app_id": self.triage_dify_app_id,
                "specialists": self.specialists,
                "handoff_edges": self.handoff_edges,
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
        *,
        project_triage: bool = True,
        inject_handoffs: bool = False,
        dify_base_url: str | None = None,
        routing_mode: str = "chain",
        routing_condition_mode: str = "contains",
        routing_fallback_agent: str | None = None,
    ) -> ProjectionResult:
        slug = system_slug or self._slugify(spec.name)
        existing = self._read_mapping(slug)
        prior_specialists = (existing.specialists if existing else {})
        prior_triage_id = existing.triage_dify_app_id if existing else None

        result = ProjectionResult(
            system_slug=slug,
            projected_at=_utc_now(),
        )

        new_mapping: dict[str, str] = dict(prior_specialists)

        # ----- specialists -----
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

        # ----- triage (W3 D2) -----
        triage_app_id = prior_triage_id
        if project_triage:
            triage_specimen = f"{slug}.triage"
            triage_nl = self._build_triage_nl(spec)
            triage_outcome = await self._project_one(
                triage_specimen, triage_nl, spec.triage.name,
            )
            result.triage = TriageProjection(
                specimen_id=triage_specimen,
                dify_app_id=triage_outcome.get("dify_app_id", ""),
                status=triage_outcome.get("status", "unknown"),
                yaml_chars=triage_outcome.get("yaml_chars", 0),
                is_first_deploy=triage_outcome.get("is_first_deploy", False),
                deploy_count=triage_outcome.get("deploy_count", 0),
                error=triage_outcome.get("error", ""),
            )
            if triage_outcome.get("dify_app_id"):
                triage_app_id = triage_outcome["dify_app_id"]

        # ----- handoff edges metadata (W3 D2) -----
        result.handoff_edges = self._build_handoff_metadata(
            spec, triage_app_id, new_mapping,
        )

        # ----- handoff webhook injection (W3 D4 + W5 D4) -----
        if inject_handoffs and result.handoff_edges:
            result.injected_agents = await self._inject_and_republish(
                slug=slug,
                spec=spec,
                triage_app_id=triage_app_id,
                specialist_app_ids=new_mapping,
                handoff_edges=result.handoff_edges,
                dify_base_url=dify_base_url,
                routing_mode=routing_mode,
                routing_condition_mode=routing_condition_mode,
                routing_fallback_agent=routing_fallback_agent,
            )

        mapping = _MappingFile(
            system_slug=slug,
            updated_at=result.projected_at,
            triage_dify_app_id=triage_app_id,
            specialists=new_mapping,
            handoff_edges=[asdict(e) for e in result.handoff_edges],
        )
        result.mapping_path = self._write_mapping(mapping)
        return result

    async def _inject_and_republish(
        self,
        *,
        slug: str,
        spec: "MultiAgentSpec",
        triage_app_id: str | None,
        specialist_app_ids: dict[str, str],
        handoff_edges: list[HandoffEdgeMetadata],
        dify_base_url: str | None,
        routing_mode: str = "chain",
        routing_condition_mode: str = "contains",
        routing_fallback_agent: str | None = None,
    ) -> list[InjectedAgent]:
        """For each source agent with outgoing edges, rebuild + inject + republish.

        Splits the handoff_edges list by from_agent. For each source agent
        we re-run FactoryPipeline on the agent's nl_brief (or triage NL),
        inject the webhook chain OR a routing if-else, and DifyPublisher.publish
        under the same specimen_id (so the existing dify_app_id is reused).
        """
        from app.delivery.handoff_routing_injector import HandoffRoutingInjector
        from app.delivery.handoff_webhook_injector import HandoffWebhookInjector

        chain_injector = HandoffWebhookInjector(dify_base_url=dify_base_url)
        routing_injector = HandoffRoutingInjector(dify_base_url=dify_base_url)

        # group edges by source agent
        by_source: dict[str, list[HandoffEdgeMetadata]] = {}
        for e in handoff_edges:
            by_source.setdefault(e.from_agent, []).append(e)

        out: list[InjectedAgent] = []
        for source_agent, edges in by_source.items():
            if source_agent == "triage":
                source_app_id = triage_app_id
                source_specimen = f"{slug}.triage"
                source_nl = self._build_triage_nl(spec)
                source_name = spec.triage.name
            else:
                source_app_id = specialist_app_ids.get(source_agent)
                source_specimen = f"{slug}.{source_agent}"
                sp = next((s for s in spec.specialists if s.id == source_agent), None)
                if sp is None:
                    out.append(InjectedAgent(
                        source_agent=source_agent,
                        republish_error="unknown source agent (not in spec)",
                    ))
                    continue
                source_nl = sp.nl_brief
                source_name = sp.name

            if not source_app_id:
                out.append(InjectedAgent(
                    source_agent=source_agent,
                    republish_error="source not yet published (skipping injection)",
                ))
                continue

            # rebuild the YAML so we have something fresh to inject into
            try:
                build_result = await self._build(source_nl)
            except Exception as exc:  # noqa: BLE001
                out.append(InjectedAgent(
                    source_agent=source_agent,
                    republish_error=f"rebuild failed: {exc}",
                ))
                continue
            outputs = build_result.get("outputs", {})
            yaml_text = outputs.get("dify") or build_result.get("dsl") or ""
            if not yaml_text:
                out.append(InjectedAgent(
                    source_agent=source_agent,
                    republish_error="rebuild produced no YAML",
                ))
                continue

            # Switch between chain (W3 D3) and routing (W5 D1-D3) injectors
            if routing_mode == "switch":
                rinj = routing_injector.inject_routing(
                    source_agent, yaml_text, edges,
                    condition_mode=routing_condition_mode,
                    fallback_agent=routing_fallback_agent,
                )
                if not rinj.did_inject:
                    out.append(InjectedAgent(
                        source_agent=source_agent,
                        republish_error=f"routing injector skipped: {rinj.skipped_reason}",
                    ))
                    continue
                injected_targets = [r.target_agent for r in rinj.routes]
                yaml_to_publish = rinj.yaml_out
            else:
                inj_res = chain_injector.inject(source_agent, yaml_text, edges)
                if not inj_res.did_inject:
                    out.append(InjectedAgent(
                        source_agent=source_agent,
                        republish_error=f"chain injector skipped: {inj_res.skipped_reason}",
                    ))
                    continue
                injected_targets = [w.target_agent for w in inj_res.injected]
                yaml_to_publish = inj_res.yaml_out

            try:
                pub_res = self.publisher.publish(
                    source_specimen, yaml_to_publish, app_name=source_name,
                )
            except RuntimeError as exc:  # noqa: BLE001
                out.append(InjectedAgent(
                    source_agent=source_agent,
                    injected_targets=injected_targets,
                    republish_error=f"republish failed: {exc}",
                ))
                continue

            out.append(InjectedAgent(
                source_agent=source_agent,
                injected_targets=injected_targets,
                republish_status=pub_res.status,
                republish_error=pub_res.error,
            ))
        return out

    def _build_triage_nl(self, spec: "MultiAgentSpec") -> str:
        """Synthesize an NL brief for the triage agent.

        Triage's job is to read user input, classify it, and route to the
        right specialist. We turn that into a runnable single-agent brief
        the FactoryPipeline can compile into a Dify workflow.
        """
        roster = ", ".join(
            f"{s.id} ({s.agent_class}): {s.description.strip().splitlines()[0][:80]}"
            for s in spec.specialists
        )
        return (
            f"读取用户输入,作为路由分类器,输出应转交给以下哪个 specialist:\n"
            f"{roster}\n"
            f"prompt_id={spec.triage.system_prompt_id}; "
            f"fallback_message={spec.triage.fallback_message!r}."
        )

    @staticmethod
    def _build_handoff_metadata(
        spec: "MultiAgentSpec",
        triage_app_id: str | None,
        specialist_app_ids: dict[str, str],
    ) -> list[HandoffEdgeMetadata]:
        """Map every handoff edge in spec to its runtime Dify endpoints.

        Sources:
          1. triage.initial_handoff_targets  (from triage -> specialist)
          2. specialist.handoff_targets      (specialist -> specialist)
          3. spec.handoffs                   (global edges, when="..." copied)
        """
        edges: list[HandoffEdgeMetadata] = []

        for target in spec.triage.initial_handoff_targets:
            edges.append(HandoffEdgeMetadata(
                from_agent="triage",
                to_agent=target,
                from_dify_app_id=triage_app_id,
                to_dify_app_id=specialist_app_ids.get(target),
                when="initial routing from triage",
            ))

        for s in spec.specialists:
            for target in s.handoff_targets:
                edges.append(HandoffEdgeMetadata(
                    from_agent=s.id,
                    to_agent=target,
                    from_dify_app_id=specialist_app_ids.get(s.id),
                    to_dify_app_id=specialist_app_ids.get(target),
                    when=f"{s.id} declared handoff target",
                ))

        for edge in spec.handoffs:
            from_id = getattr(edge, "from_specialist", None) or ""
            to_id = getattr(edge, "to_specialist", None) or ""
            when = getattr(edge, "when", "")
            edges.append(HandoffEdgeMetadata(
                from_agent=from_id,
                to_agent=to_id,
                from_dify_app_id=specialist_app_ids.get(from_id),
                to_dify_app_id=specialist_app_ids.get(to_id),
                when=when,
            ))

        # dedup by (from_agent, to_agent), keeping the first 'when' description
        seen: set[tuple[str, str]] = set()
        out: list[HandoffEdgeMetadata] = []
        for e in edges:
            key = (e.from_agent, e.to_agent)
            if key in seen or not e.from_agent or not e.to_agent:
                continue
            seen.add(key)
            out.append(e)
        return out

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
