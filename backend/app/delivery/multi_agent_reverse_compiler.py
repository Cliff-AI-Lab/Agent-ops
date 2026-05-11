"""MultiAgentReverseCompiler - Phase 10 W4 D3 system-level reverse compile.

After W3 ships the multi-agent canvas projection, a human in Dify can
edit any specialist's workflow. This module walks the whole system and
reports per-specialist + cross-system drift in one pass:

  spec.specialists[i].tools     ←  baseline atom set per specialist
  Dify export of app_id_i        ←  current atom set per specialist
  → ReverseDiff per agent
  → MultiAgentSystemDiff (aggregate)

The single-agent ReverseCompiler (Phase 10 W1 Day 4) diffs an authoritative
ResolvedDAG vs an edited YAML. For multi-agent we don't have a single
ResolvedDAG to anchor against, so we use MultiAgentSpec as the
declarative baseline: each specialist's ``tools`` list is the canonical
atom set the factory intended. Anything in the Dify export not present
in tools is an addition; anything missing is a removal.

R3 reuse audit:
  - Reuses ReverseCompiler._extract_yaml_atoms (parses Dify YAML for
    node.data._factory.asset_id sequences)
  - Reuses DifyPublisher.drift_check / _get_json for fetching exports
  - Reuses MultiAgentSpec (Phase 7 IR)
  - Mapping JSON written by MultiAgentDifyProjector (W3 D2)
  - No schema migration
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from app.delivery.reverse_compiler import ReverseCompiler

if TYPE_CHECKING:
    from app.core.pipelines.factory.ir.multi_agent import MultiAgentSpec
    from app.delivery.dify_publisher import DifyPublisher

logger = logging.getLogger(__name__)


@dataclass
class AgentReverseDiff:
    """Per-agent reverse diff result."""

    agent_id: str
    dify_app_id: str
    baseline_atoms: list[str] = field(default_factory=list)
    edited_atoms: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    human_tuned: bool = False
    fetch_error: str = ""

    def short_summary(self) -> str:
        if self.fetch_error:
            return f"fetch error: {self.fetch_error}"
        if not self.human_tuned:
            return "no change"
        parts: list[str] = []
        if self.added:
            parts.append(f"+{len(self.added)} ({', '.join(self.added)})")
        if self.removed:
            parts.append(f"-{len(self.removed)} ({', '.join(self.removed)})")
        return "; ".join(parts) or "no atom change"


@dataclass
class MultiAgentSystemDiff:
    """Aggregate diff for an entire multi-agent system."""

    system_slug: str
    per_agent: list[AgentReverseDiff] = field(default_factory=list)
    mapping_path: Path | None = None

    @property
    def tuned_agents(self) -> list[str]:
        return [d.agent_id for d in self.per_agent if d.human_tuned]

    @property
    def fetch_failures(self) -> list[str]:
        return [d.agent_id for d in self.per_agent if d.fetch_error]

    @property
    def system_human_tuned(self) -> bool:
        return bool(self.tuned_agents)

    def short_summary(self) -> str:
        if not self.per_agent:
            return f"no agents to compare in system={self.system_slug}"
        if self.fetch_failures:
            fetched = len(self.per_agent) - len(self.fetch_failures)
        else:
            fetched = len(self.per_agent)
        tuned = len(self.tuned_agents)
        parts = [
            f"system={self.system_slug}",
            f"agents={fetched}/{len(self.per_agent)} fetched",
        ]
        if tuned:
            parts.append(f"{tuned} tuned ({', '.join(self.tuned_agents)})")
        else:
            parts.append("no human edits detected")
        if self.fetch_failures:
            parts.append(f"{len(self.fetch_failures)} fetch failed")
        return " · ".join(parts)


class MultiAgentReverseCompiler:
    """Stateless. Construct once per diff.

    Args:
        publisher: DifyPublisher (used to fetch each specialist's
                   exported YAML via the console API)
    """

    def __init__(self, publisher: "DifyPublisher"):
        self.publisher = publisher
        self._single = ReverseCompiler()

    def diff_system(
        self,
        spec: "MultiAgentSpec",
        system_slug: str,
        mapping_path: Path | str,
    ) -> MultiAgentSystemDiff:
        """Compare every projected agent against its declarative baseline.

        Args:
            spec: the original MultiAgentSpec (baseline tools per specialist)
            system_slug: used for trace + logging
            mapping_path: path to <slug>.multi-agent.json produced by
                          MultiAgentDifyProjector
        """
        mapping = self._load_mapping(mapping_path)
        result = MultiAgentSystemDiff(
            system_slug=system_slug,
            mapping_path=Path(mapping_path),
        )
        if mapping is None:
            return result

        triage_app_id = mapping.get("triage_dify_app_id")
        if triage_app_id:
            result.per_agent.append(self._diff_one(
                agent_id="triage", app_id=triage_app_id,
                baseline_atoms=[],  # triage has no declarative atom list
            ))

        specialist_ids = mapping.get("specialists") or {}
        spec_by_id = {s.id: s for s in spec.specialists}
        for agent_id, app_id in specialist_ids.items():
            if not app_id:
                result.per_agent.append(AgentReverseDiff(
                    agent_id=agent_id, dify_app_id="",
                    fetch_error="missing dify_app_id in mapping",
                ))
                continue
            baseline = sorted(spec_by_id.get(agent_id).tools) if agent_id in spec_by_id else []
            result.per_agent.append(self._diff_one(
                agent_id=agent_id, app_id=app_id, baseline_atoms=baseline,
            ))
        return result

    # ----- internals ----------------------------------------------------

    def _diff_one(
        self, *, agent_id: str, app_id: str, baseline_atoms: list[str],
    ) -> AgentReverseDiff:
        edited_yaml, fetch_err = self._fetch_export(app_id)
        if fetch_err:
            return AgentReverseDiff(
                agent_id=agent_id, dify_app_id=app_id,
                baseline_atoms=baseline_atoms,
                fetch_error=fetch_err,
            )

        edited_atoms, parse_err = self._single._extract_yaml_atoms(edited_yaml)  # noqa: SLF001
        if parse_err:
            return AgentReverseDiff(
                agent_id=agent_id, dify_app_id=app_id,
                baseline_atoms=baseline_atoms,
                fetch_error=f"parse: {parse_err}",
            )

        baseline_set = set(baseline_atoms)
        edited_set = set(edited_atoms)
        added = sorted(edited_set - baseline_set)
        removed = sorted(baseline_set - edited_set)
        human_tuned = bool(added or removed)

        return AgentReverseDiff(
            agent_id=agent_id, dify_app_id=app_id,
            baseline_atoms=baseline_atoms, edited_atoms=edited_atoms,
            added=added, removed=removed, human_tuned=human_tuned,
        )

    def _fetch_export(self, app_id: str) -> tuple[str, str]:
        """Fetch Dify export YAML for one app. Returns (yaml_text, error)."""
        # Login + admin idempotent
        try:
            self.publisher._wait_console_ready()  # noqa: SLF001
            self.publisher._ensure_admin()  # noqa: SLF001
            self.publisher._ensure_logged_in()  # noqa: SLF001
        except RuntimeError as exc:
            return "", f"console not ready: {exc}"

        url = f"{self.publisher.base_url}/console/api/apps/{app_id}/export"
        code, body = self.publisher._get_json(  # noqa: SLF001
            url, headers=self.publisher._csrf_headers(),  # noqa: SLF001
        )
        if code != 200:
            return "", f"export HTTP {code}: {body}"
        if not isinstance(body, dict):
            return "", f"export unexpected shape: {body}"
        data = body.get("data") or ""
        if not data:
            return "", f"export had no 'data' field; body={body}"
        return data, ""

    @staticmethod
    def _load_mapping(path: Path | str) -> dict | None:
        p = Path(path)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            logger.warning("mapping %s unreadable: %s", p, exc)
            return None
