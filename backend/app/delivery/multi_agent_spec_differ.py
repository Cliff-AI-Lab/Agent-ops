"""MultiAgentSpecDiffer - Phase 10 W2 D3 multi-agent spec.json diff.

Sister to ReverseCompiler (Phase 10 D4) but for multi-agent systems.
ReverseCompiler diffs Dify YAML against a ResolvedDAG; this differ
diffs an edited MultiAgentSpec against the original one to show:

  - which specialists were added / removed
  - which specialists' handoff_targets shifted (handoff chain mutated)
  - which specialists' tools shifted (atom set per specialist mutated)
  - whether the triage's initial_handoff_targets changed
  - whether guardrails / shared_context blocks shifted

R3 reuse audit:
  - MultiAgentSpec / SpecialistSpec / TriageSpec from Phase 7 IR (V2.1.0)
  - AgentDependencyAnalyzer (Phase 10 W2 D1) auto-fed with the changed
    specialist list to surface blast radius for each delta
  - No schema change

Used by:
  - Phase 10 W2 D2 pipeline (when a multi-agent system gets re-built;
    differ runs to see what the user-edited / human-tuned)
  - Phase 10 W3 (multi-agent Dify projection drift detection)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.pipelines.factory.ir.multi_agent import MultiAgentSpec


@dataclass
class SpecialistDelta:
    """Per-specialist field-level diff."""

    specialist_id: str
    handoff_targets_added: list[str] = field(default_factory=list)
    handoff_targets_removed: list[str] = field(default_factory=list)
    tools_added: list[str] = field(default_factory=list)
    tools_removed: list[str] = field(default_factory=list)
    nl_brief_changed: bool = False
    description_changed: bool = False
    agent_class_changed: bool = False

    @property
    def has_change(self) -> bool:
        return bool(
            self.handoff_targets_added
            or self.handoff_targets_removed
            or self.tools_added
            or self.tools_removed
            or self.nl_brief_changed
            or self.description_changed
            or self.agent_class_changed
        )


@dataclass
class MultiAgentSpecDiff:
    """Outcome of comparing two MultiAgentSpec instances."""

    system_slug_a: str
    system_slug_b: str
    specialists_added: list[str] = field(default_factory=list)
    specialists_removed: list[str] = field(default_factory=list)
    specialist_deltas: list[SpecialistDelta] = field(default_factory=list)
    triage_handoff_added: list[str] = field(default_factory=list)
    triage_handoff_removed: list[str] = field(default_factory=list)
    triage_prompt_changed: bool = False
    guardrails_changed: bool = False
    shared_context_changed: bool = False

    @property
    def human_tuned(self) -> bool:
        return bool(
            self.specialists_added
            or self.specialists_removed
            or any(d.has_change for d in self.specialist_deltas)
            or self.triage_handoff_added
            or self.triage_handoff_removed
            or self.triage_prompt_changed
            or self.guardrails_changed
            or self.shared_context_changed
        )

    def short_summary(self) -> str:
        if not self.human_tuned:
            return "no spec change"
        parts: list[str] = []
        if self.specialists_added:
            parts.append(f"+{len(self.specialists_added)} specialists ({', '.join(self.specialists_added)})")
        if self.specialists_removed:
            parts.append(f"-{len(self.specialists_removed)} specialists ({', '.join(self.specialists_removed)})")
        deltas_with_change = [d for d in self.specialist_deltas if d.has_change]
        if deltas_with_change:
            parts.append(f"{len(deltas_with_change)} specialists field-changed")
        if self.triage_handoff_added or self.triage_handoff_removed:
            parts.append("triage handoff targets shifted")
        if self.triage_prompt_changed:
            parts.append("triage prompt changed")
        if self.guardrails_changed:
            parts.append("guardrails changed")
        if self.shared_context_changed:
            parts.append("shared_context changed")
        return "; ".join(parts)


class MultiAgentSpecDiffer:
    """Stateless. Construct once per diff."""

    def diff(
        self,
        original: MultiAgentSpec,
        edited: MultiAgentSpec,
    ) -> MultiAgentSpecDiff:
        out = MultiAgentSpecDiff(
            system_slug_a=original.name,
            system_slug_b=edited.name,
        )

        orig_by_id = {s.id: s for s in original.specialists}
        edit_by_id = {s.id: s for s in edited.specialists}
        orig_ids = set(orig_by_id)
        edit_ids = set(edit_by_id)

        out.specialists_added = sorted(edit_ids - orig_ids)
        out.specialists_removed = sorted(orig_ids - edit_ids)

        for sid in sorted(orig_ids & edit_ids):
            delta = self._diff_specialist(orig_by_id[sid], edit_by_id[sid])
            if delta.has_change:
                out.specialist_deltas.append(delta)

        # Triage delta
        orig_t = set(original.triage.initial_handoff_targets)
        edit_t = set(edited.triage.initial_handoff_targets)
        out.triage_handoff_added = sorted(edit_t - orig_t)
        out.triage_handoff_removed = sorted(orig_t - edit_t)
        out.triage_prompt_changed = (
            original.triage.system_prompt_id != edited.triage.system_prompt_id
        )

        # Guardrails: compare as set of (kind, blocking, description)
        out.guardrails_changed = self._guardrails_changed(original, edited)

        # Shared context: compare on (name, type) tuples
        out.shared_context_changed = self._shared_context_changed(original, edited)

        return out

    # ----- internals -----------------------------------------------------

    @staticmethod
    def _diff_specialist(a, b) -> SpecialistDelta:
        a_targets = set(a.handoff_targets)
        b_targets = set(b.handoff_targets)
        a_tools = set(a.tools)
        b_tools = set(b.tools)
        return SpecialistDelta(
            specialist_id=a.id,
            handoff_targets_added=sorted(b_targets - a_targets),
            handoff_targets_removed=sorted(a_targets - b_targets),
            tools_added=sorted(b_tools - a_tools),
            tools_removed=sorted(a_tools - b_tools),
            nl_brief_changed=a.nl_brief != b.nl_brief,
            description_changed=a.description != b.description,
            agent_class_changed=a.agent_class != b.agent_class,
        )

    @staticmethod
    def _guardrails_changed(a: MultiAgentSpec, b: MultiAgentSpec) -> bool:
        def key(g) -> tuple:
            return (g.kind, g.blocking, g.description)

        return {key(g) for g in a.guardrails} != {key(g) for g in b.guardrails}

    @staticmethod
    def _shared_context_changed(a: MultiAgentSpec, b: MultiAgentSpec) -> bool:
        def key(s) -> tuple:
            return (s.name, s.type)

        return {key(s) for s in a.shared_context} != {key(s) for s in b.shared_context}
