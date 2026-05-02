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
    ) -> None:
        """All sub-components injectable. Defaults wire V2.1.0 W1+W2 impls.

        Note: when both single_pipeline and multi_pipeline are supplied, their
        internal routers are NOT used; Switcher's own router is the source of truth.
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
