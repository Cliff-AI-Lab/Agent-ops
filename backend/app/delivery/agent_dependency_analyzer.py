"""AgentDependencyAnalyzer - Phase 10 W2 multi-agent blast-radius analysis.

When a single specialist (or the triage) inside a multi-agent system is
adjusted, we need to surface what else changes with it. Phase 10 user
direction:

    "多智能路径也要走通,多智能体中单个智能体通过对话调整完对其他
     智能体的影响也要体现"

This module computes that blast radius from a MultiAgentSpec without
running anything - pure graph + set algebra over the spec.

R3 reuse audit:
  - MultiAgentSpec / SpecialistSpec / TriageSpec already exist
    (Phase 7 IR, V2.1.0)
  - HandoffEdge already exists in the same module
  - No schema migration

The 4 impact kinds:

  1. direct_upstream   agents that handoff TO the changed one
  2. direct_downstream agents the changed one handoffs TO
  3. tool_overlap      other agents that share at least one tool/atom_id
  4. transitive_reach  BFS over handoff edges from the changed agent
                       (excludes the agent itself)

Surfaced via the same trace bus everything else uses:
  emit("L3", "MultiAgent", "multi_agent_dependency_impact", payload)
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Literal

from app.core.pipelines.factory.ir.multi_agent import MultiAgentSpec


ImpactKind = Literal[
    "handoff_chain",
    "shared_tool",
    "transitive",
    "no_impact",
]


@dataclass
class ToolOverlap:
    """Other agent that shares one or more tools with the changed agent."""

    other_agent_id: str
    shared_tools: list[str] = field(default_factory=list)


@dataclass
class AgentImpactReport:
    """Blast radius of changing one agent inside a multi-agent system."""

    changed_agent: str
    direct_upstream: list[str] = field(default_factory=list)
    direct_downstream: list[str] = field(default_factory=list)
    tool_overlap: list[ToolOverlap] = field(default_factory=list)
    transitive_reach: list[str] = field(default_factory=list)
    impact_kinds: list[ImpactKind] = field(default_factory=list)

    @property
    def affected_count(self) -> int:
        seen: set[str] = set(self.direct_upstream)
        seen.update(self.direct_downstream)
        seen.update(o.other_agent_id for o in self.tool_overlap)
        seen.update(self.transitive_reach)
        seen.discard(self.changed_agent)
        return len(seen)

    def short_summary(self) -> str:
        if not self.impact_kinds or self.impact_kinds == ["no_impact"]:
            return f"changing {self.changed_agent} affects no other agents"
        parts: list[str] = []
        if self.direct_upstream:
            parts.append(f"upstream→ {len(self.direct_upstream)} ({', '.join(self.direct_upstream)})")
        if self.direct_downstream:
            parts.append(f"→downstream {len(self.direct_downstream)} ({', '.join(self.direct_downstream)})")
        if self.tool_overlap:
            others = ", ".join(o.other_agent_id for o in self.tool_overlap)
            parts.append(f"shared-tool with {len(self.tool_overlap)} ({others})")
        if self.transitive_reach:
            parts.append(f"transitive reach: {len(self.transitive_reach)}")
        return "; ".join(parts)


class AgentDependencyAnalyzer:
    """Stateless. Construct once per analysis."""

    TRIAGE_ID = "triage"  # implicit id used in spec.handoffs from triage

    def analyze_change(
        self,
        spec: MultiAgentSpec,
        changed_agent_id: str,
    ) -> AgentImpactReport:
        if not self._agent_exists(spec, changed_agent_id):
            return AgentImpactReport(
                changed_agent=changed_agent_id,
                impact_kinds=["no_impact"],
            )

        upstream = self._upstream_of(spec, changed_agent_id)
        downstream = self._downstream_of(spec, changed_agent_id)
        tool_overlap = self._tool_overlap(spec, changed_agent_id)
        transitive = self._transitive_reach(spec, changed_agent_id)

        kinds: list[ImpactKind] = []
        if upstream or downstream:
            kinds.append("handoff_chain")
        if tool_overlap:
            kinds.append("shared_tool")
        # transitive is interesting only when it adds beyond direct neighbors
        direct = set(upstream) | set(downstream)
        extra = [a for a in transitive if a not in direct]
        if extra:
            kinds.append("transitive")
        if not kinds:
            kinds = ["no_impact"]

        return AgentImpactReport(
            changed_agent=changed_agent_id,
            direct_upstream=upstream,
            direct_downstream=downstream,
            tool_overlap=tool_overlap,
            transitive_reach=transitive,
            impact_kinds=kinds,
        )

    # ----- internals -----------------------------------------------------

    def _agent_exists(self, spec: MultiAgentSpec, aid: str) -> bool:
        if aid == self.TRIAGE_ID:
            return True
        return any(s.id == aid for s in spec.specialists)

    def _upstream_of(
        self, spec: MultiAgentSpec, aid: str
    ) -> list[str]:
        ups: set[str] = set()
        if aid in spec.triage.initial_handoff_targets:
            ups.add(self.TRIAGE_ID)
        for s in spec.specialists:
            if aid in s.handoff_targets and s.id != aid:
                ups.add(s.id)
        # also fold spec.handoffs (global edges)
        for edge in spec.handoffs:
            from_id, to_id = self._edge_endpoints(edge)
            if to_id == aid and from_id != aid:
                ups.add(from_id)
        return sorted(ups)

    def _downstream_of(
        self, spec: MultiAgentSpec, aid: str
    ) -> list[str]:
        downs: set[str] = set()
        if aid == self.TRIAGE_ID:
            downs.update(spec.triage.initial_handoff_targets)
        else:
            spec_obj = self._spec_for(spec, aid)
            if spec_obj is not None:
                downs.update(spec_obj.handoff_targets)
        for edge in spec.handoffs:
            from_id, to_id = self._edge_endpoints(edge)
            if from_id == aid and to_id != aid:
                downs.add(to_id)
        downs.discard(aid)
        return sorted(downs)

    def _tool_overlap(
        self, spec: MultiAgentSpec, aid: str
    ) -> list[ToolOverlap]:
        if aid == self.TRIAGE_ID:
            return []  # triage has no tools field
        target = self._spec_for(spec, aid)
        if target is None:
            return []
        target_tools = set(target.tools)
        if not target_tools:
            return []
        out: list[ToolOverlap] = []
        for s in spec.specialists:
            if s.id == aid:
                continue
            shared = sorted(target_tools & set(s.tools))
            if shared:
                out.append(ToolOverlap(other_agent_id=s.id, shared_tools=shared))
        return out

    def _transitive_reach(
        self, spec: MultiAgentSpec, aid: str
    ) -> list[str]:
        """BFS down handoff edges from the changed agent, max depth unbounded."""
        adjacency = self._build_adjacency(spec)
        visited: set[str] = set()
        queue: deque[str] = deque([aid])
        while queue:
            cur = queue.popleft()
            for nxt in adjacency.get(cur, []):
                if nxt in visited or nxt == aid:
                    continue
                visited.add(nxt)
                queue.append(nxt)
        return sorted(visited)

    def _build_adjacency(self, spec: MultiAgentSpec) -> dict[str, list[str]]:
        adj: dict[str, list[str]] = {self.TRIAGE_ID: list(spec.triage.initial_handoff_targets)}
        for s in spec.specialists:
            adj.setdefault(s.id, []).extend(s.handoff_targets)
        for edge in spec.handoffs:
            from_id, to_id = self._edge_endpoints(edge)
            adj.setdefault(from_id, []).append(to_id)
        # dedup
        for k in adj:
            adj[k] = list(dict.fromkeys(adj[k]))
        return adj

    @staticmethod
    def _spec_for(spec: MultiAgentSpec, aid: str):
        for s in spec.specialists:
            if s.id == aid:
                return s
        return None

    @staticmethod
    def _edge_endpoints(edge) -> tuple[str, str]:
        """Read endpoints from a HandoffEdge.

        Current model uses ``from_specialist`` / ``to_specialist``; we also
        probe a few legacy attribute names for forward compat in case the
        IR field names change again.
        """
        for f_attr in ("from_specialist", "from_agent", "from_id", "source"):
            if hasattr(edge, f_attr):
                f = getattr(edge, f_attr)
                break
        else:
            f = ""
        for t_attr in ("to_specialist", "to_agent", "to_id", "target"):
            if hasattr(edge, t_attr):
                t = getattr(edge, t_attr)
                break
        else:
            t = ""
        return str(f), str(t)
