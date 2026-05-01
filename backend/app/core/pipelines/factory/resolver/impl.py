"""ResolverImpl: orchestrates recall -> rank -> bind for each step + Target Selector.

Per-step pipeline:
  1. recall_for_step(step, search_engine) -> candidates
  2. rank_candidates(step, candidates, llm) -> selection
  3. build_node(step, selection) -> ResolvedNode

After all nodes built:
  4. build_edges(intent, nodes, atoms) -> edges (with type_check)
  5. select_target(intent, nodes, atoms) -> dify/n8n/hybrid

Emits L3 trace events at each major boundary.
"""

from __future__ import annotations

from app.core.llm.client import LLMClient
from app.core.llm.model_router import ModelRouter
from app.core.pipelines.factory.ir import (
    ResolvedDAG,
    ResolvedNode,
    StructuredIntent,
)
from app.core.pipelines.factory.resolver.bind import (
    build_edges,
    build_node,
    select_target,
)
from app.core.pipelines.factory.resolver.rank import rank_candidates
from app.core.pipelines.factory.resolver.recall import recall_for_step
from app.core.trace.bus import emit
from app.registry.search_engine import SearchEngineImpl


class ResolverImpl:
    def __init__(
        self,
        search_engine: SearchEngineImpl,
        llm_client: LLMClient | None = None,
        model_router: ModelRouter | None = None,
    ) -> None:
        self._search = search_engine
        self._llm = llm_client
        self._router = model_router or ModelRouter()

    async def resolve(self, intent: StructuredIntent) -> ResolvedDAG:
        emit(
            "L3",
            "Resolver",
            "resolve_start",
            f"goal={intent.goal!r} steps={len(intent.steps)}",
        )

        if self._llm is None:
            self._llm = LLMClient()
        rank_model = self._router.resolve("结构化抽取", "中", ["json_response_format"])

        nodes_by_id: dict[str, ResolvedNode] = {}
        issues: list[dict] = []

        atoms_by_id = {a.asset_id: a for a in self._search._atoms.values()}

        for step in intent.steps:
            candidates = recall_for_step(step, self._search, top_k=5)
            if not candidates:
                issues.append(
                    {
                        "kind": "no_candidate",
                        "step_id": step.id,
                        "verb": step.verb,
                        "subcategory_hint": step.suggested_subcategory,
                    }
                )
                emit(
                    "L3",
                    "Resolver",
                    "no_candidate",
                    f"step={step.id} verb={step.verb!r}",
                )
                continue

            try:
                selection = await rank_candidates(
                    step=step,
                    candidates=candidates,
                    llm_client=self._llm,
                    model=rank_model,
                )
            except Exception as exc:
                emit("L3", "Resolver", "rank_fail", f"step={step.id} err={exc}")
                atom, score = candidates[0]
                from app.core.pipelines.factory.resolver.rank import _Selection

                selection = _Selection(
                    atom=atom,
                    confidence=score * 0.5,
                    reason=f"rank failed ({exc}); fell back to top recall",
                )

            node = build_node(
                step=step,
                atom=selection.atom,
                confidence=selection.confidence,
                reason=selection.reason,
                llm_task_type=selection.llm_task_type,
                llm_size=selection.llm_size,
                prompt_id=None,
            )
            nodes_by_id[step.id] = node
            atoms_by_id[selection.atom.asset_id] = selection.atom

            if selection.confidence < 0.6:
                issues.append(
                    {
                        "kind": "low_confidence",
                        "step_id": step.id,
                        "asset_id": selection.atom.asset_id,
                        "confidence": selection.confidence,
                    }
                )

        edges = build_edges(intent, nodes_by_id, atoms_by_id)
        target, target_split = select_target(intent, nodes_by_id, atoms_by_id)

        adapter_count = sum(1 for e in edges if e.needs_adapter)
        if adapter_count:
            issues.append(
                {"kind": "type_adapters_needed", "count": adapter_count}
            )

        emit(
            "L3",
            "Resolver",
            "resolve_done",
            f"nodes={len(nodes_by_id)} edges={len(edges)} target={target} issues={len(issues)}",
        )

        return ResolvedDAG(
            intent_ref=intent.raw_user_input[:60],
            pattern_id=None,
            nodes=list(nodes_by_id.values()),
            edges=edges,
            target=target,
            target_split=target_split,
            issues=issues,
        )
