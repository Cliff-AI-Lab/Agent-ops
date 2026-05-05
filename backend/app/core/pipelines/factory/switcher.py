"""FactorySwitcher - top-level NL entry that auto-routes to single or multi-agent.

Per [[Phase-7-多智能体与行业理解]] § 双阶段调度:
  Stage 1 IndustryRouter classifies NL once.
  Stage 2 dispatches to FactoryPipeline (single) or MultiAgentFactoryPipeline (multi)
          based on classification.is_multi_agent.

This is the public entrypoint for V2.1.0+. Single-LLM-call routing — the
classification result is forwarded to whichever pipeline is selected so it
doesn't run again.

Output schema (always):
  {
    "path": "single" | "multi",
    "classification": IndustryClassification,
    ... pipeline-specific fields:
      single -> intent, dag, target, outputs (dify+n8n), validation, ...
      multi  -> spec, source_code, system_slug
  }
"""
from __future__ import annotations

from typing import Any, Optional

from app.core.governance import (
    CostBudget,
    CostBudgetExceeded,
    CostEstimator,
    CostLedger,
)
from app.core.llm.client import LLMClient
from app.core.llm.model_router import ModelRouter
from app.core.pipelines.factory.industry import (
    IndustryClassification,
    IndustryRouter,
    IndustryRouterImpl,
)
from app.core.pipelines.factory.multi_agent_pipeline import MultiAgentFactoryPipeline
from app.core.pipelines.factory.pipeline import FactoryPipeline
from app.core.trace.bus import emit


class FactorySwitcher:
    """Top-level orchestrator: NL -> single OR multi based on Router verdict."""

    def __init__(
        self,
        single_pipeline: Optional[FactoryPipeline] = None,
        multi_pipeline: Optional[MultiAgentFactoryPipeline] = None,
        router: Optional[IndustryRouter] = None,
        llm_client: Optional[LLMClient] = None,
        model_router: Optional[ModelRouter] = None,
        cost_estimator: Optional[CostEstimator] = None,
        cost_budget: Optional[CostBudget] = None,
        cost_ledger: Optional[CostLedger] = None,
        tenant_id: str = "default",
    ) -> None:
        """All sub-components injectable. Defaults wire V2.1.0 W1+W2 impls.

        Note: when both single_pipeline and multi_pipeline are supplied, their
        internal routers are NOT used; Switcher's own router is the source of truth.

        cost_estimator + cost_budget are V2.2 governance gate (optional). When
        BOTH supplied, Switcher runs a pre-flight cost estimate and refuses the
        build (raises CostBudgetExceeded) if the estimate exceeds the budget.
        Either alone disables the gate.

        cost_ledger (V2.2 W3) is optional persistent log. When supplied, every
        cost-gated decision (approved or refused) is recorded for daily/monthly
        rolling budget enforcement. tenant_id partitions ledger entries.
        """
        shared_model_router = model_router or ModelRouter()
        self._router = router or IndustryRouterImpl(
            llm_client=llm_client, model_router=shared_model_router
        )
        # Lazy / dependency-injected pipelines
        self._single = single_pipeline
        self._multi = multi_pipeline
        self._llm = llm_client
        self._shared_router = shared_model_router
        # Cost gate (V2.2): only active when BOTH estimator and budget supplied
        self._cost_estimator = cost_estimator
        self._cost_budget = cost_budget
        self._ledger = cost_ledger
        self._tenant = tenant_id

    async def build(self, nl: str) -> dict[str, Any]:
        if not nl or not nl.strip():
            raise ValueError("nl cannot be empty")

        emit("L3", "FactorySwitcher", "classify_start", f"nl_len={len(nl)}")
        classification: IndustryClassification = await self._router.classify(nl)
        emit(
            "L3",
            "FactorySwitcher",
            "route_decision",
            f"is_multi_agent={classification.is_multi_agent} "
            f"industry={classification.industry_code}",
            data={"path": "multi" if classification.is_multi_agent else "single"},
        )

        # V2.2 governance gate: pre-flight cost estimate (post-classify so we
        # know which path's cost to estimate, but pre-build so we don't waste
        # the bigger LLM calls if the budget refuses).
        if self._cost_estimator is not None and self._cost_budget is not None:
            if classification.is_multi_agent:
                estimate = self._cost_estimator.estimate_multi(nl)
            else:
                estimate = self._cost_estimator.estimate_single(nl)

            # RollingBudget supports check_async; ThresholdCostBudget is sync-only.
            check_async = getattr(self._cost_budget, "check_async", None)
            if check_async is not None:
                decision = await check_async(estimate)
            else:
                decision = self._cost_budget.check(estimate)

            path_label = "multi" if classification.is_multi_agent else "single"
            # V2.2 W3: record to ledger if available (approved + refused both)
            if self._ledger is not None:
                await self._ledger.record(
                    estimate=estimate,
                    approved=decision.approved,
                    path=path_label,
                    tenant_id=self._tenant,
                )

            if not decision.approved:
                raise CostBudgetExceeded(decision)

        if classification.is_multi_agent:
            multi = self._multi or MultiAgentFactoryPipeline(
                llm_client=self._llm, model_router=self._shared_router
            )
            inner = await multi.build_with_classification(nl, classification)
            # Don't double-store classification; multi already includes it.
            return {"path": "multi", **inner}

        single = self._single
        if single is None:
            # FactoryPipeline requires atoms_dir or pre-built components.
            raise RuntimeError(
                "FactorySwitcher needs single_pipeline injected for is_multi_agent=False NL "
                "(FactoryPipeline requires atoms_dir or pre-wired components)."
            )
        inner = await single.build(nl)
        return {"path": "single", "classification": classification, **inner}
