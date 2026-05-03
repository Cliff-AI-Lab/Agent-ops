"""MultiAgentFactoryPipeline - end-to-end Phase 7 orchestrator.

Parallel to FactoryPipeline (single-agent), this orchestrates the multi-agent
path for Phase 7 systems:

  NL --(IndustryRouter)--> Classification
                              ↓ is_multi_agent=True
                    (IndustryDesigner)--> MultiAgentSpec
                              ↓
                  (MultiAgentComposer)--> Python source string
                              ↓ deploy()
                  agents/__generated__/{slug}/main.py + metadata

Per [[Phase-7-多智能体与行业理解]] § 双阶段调度.

When `is_multi_agent=False`, callers should use FactoryPipeline instead.
A future Switcher component can auto-route. For V2.1.0 W2 the two pipelines
are explicitly separate to keep contracts simple.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.core.llm.client import LLMClient
from app.core.llm.model_router import ModelRouter
from app.core.pipelines.factory.industry import (
    IndustryClassification,
    IndustryRouter,
    IndustryRouterImpl,
)
from app.core.pipelines.factory.industry_designer import (
    DesignerRegistry,
    GeneralDesigner,
    IndustryDesigner,
    default_registry,
)
from app.core.pipelines.factory.ir.multi_agent import MultiAgentSpec
from app.core.pipelines.factory.multi_agent_composer import (
    MultiAgentComposer,
    OpenAIAgentsSDKComposerImpl,
)
from app.core.trace.bus import emit


_SLUG_RE = re.compile(r"[^a-zA-Z0-9_]+")


def _slug(s: str, max_len: int = 48) -> str:
    """Filesystem-safe slug for the generated system folder name."""
    safe = _SLUG_RE.sub("_", s.strip().lower()).strip("_")
    if not safe:
        safe = "system"
    if safe[0].isdigit():
        safe = "_" + safe
    return safe[:max_len]


class MultiAgentNotApplicable(ValueError):
    """Raised when Router decides NL is single-agent; caller should use FactoryPipeline."""


class MultiAgentFactoryPipeline:
    """End-to-end orchestrator for the multi-agent path.

    All sub-components injectable for testing. Defaults wire in the V2.1.0 W1
    seed implementations: IndustryRouterImpl + GeneralDesigner +
    OpenAIAgentsSDKComposerImpl.
    """

    def __init__(
        self,
        router: Optional[IndustryRouter] = None,
        designer: Optional[IndustryDesigner] = None,
        designer_registry: Optional[DesignerRegistry] = None,
        composer: Optional[MultiAgentComposer] = None,
        llm_client: Optional[LLMClient] = None,
        model_router: Optional[ModelRouter] = None,
        prompts: Optional[dict] = None,
        atoms: Optional[dict] = None,
    ) -> None:
        """
        designer / designer_registry are mutually exclusive:
          - designer: pin to one specific Designer (legacy V2.1.0 W1 behavior)
          - designer_registry: dispatch by classification.industry_code (V2.1.0 W3+)
          - both None: build default_registry() (only GeneralDesigner registered)
        """
        if designer is not None and designer_registry is not None:
            raise ValueError(
                "pass either designer (pinned) or designer_registry (dispatch); not both"
            )

        shared_router = model_router or ModelRouter()
        self._router = router or IndustryRouterImpl(
            llm_client=llm_client, model_router=shared_router
        )

        # Designer dispatch
        self._pinned_designer = designer
        if designer is None and designer_registry is None:
            self._designer_registry = default_registry(
                llm_client=llm_client, model_router=shared_router
            )
        else:
            self._designer_registry = designer_registry  # may be None if pinned

        # Composer is pure-template, no LLM needed; takes optional dicts.
        self._composer = composer or OpenAIAgentsSDKComposerImpl(
            prompts=prompts, atoms=atoms
        )

    async def build(self, nl: str) -> dict[str, Any]:
        """End-to-end: NL -> {classification, spec, source_code}.

        Raises:
            ValueError: empty NL.
            MultiAgentNotApplicable: classification.is_multi_agent=False.
            ValueError: Designer produced spec with structural issues.
            RuntimeError: Composer template bug (defensive).

        Does not write to disk. Use deploy() to materialize the source.
        """
        if not nl or not nl.strip():
            raise ValueError("nl cannot be empty")
        classification = await self._router.classify(nl)
        return await self.build_with_classification(nl, classification)

    async def build_with_classification(
        self, nl: str, classification: IndustryClassification
    ) -> dict[str, Any]:
        """Build with pre-computed classification (Switcher avoids re-classify).

        Same contract as build() but skips Stage 1. Useful when an outer
        orchestrator (FactorySwitcher) has already classified the NL and
        wants to dispatch to either single- or multi-agent pipelines without
        spending an extra LLM call.
        """
        if not nl or not nl.strip():
            raise ValueError("nl cannot be empty")

        emit("L3", "MultiAgentFactoryPipeline", "build_start", f"nl_len={len(nl)}")

        if not classification.is_multi_agent:
            raise MultiAgentNotApplicable(
                f"classification.is_multi_agent=False (industry={classification.industry_code} "
                f"scenario={classification.business_scenario}). "
                f"Use FactoryPipeline for single-agent NL."
            )

        # Designer selection: pinned > registry-by-industry-code
        if self._pinned_designer is not None:
            designer = self._pinned_designer
        else:
            designer = self._designer_registry.get(classification.industry_code)

        spec: MultiAgentSpec = await designer.design_multi_agent(nl, classification)
        source_code: str = self._composer.compile(spec)

        emit(
            "L3",
            "MultiAgentFactoryPipeline",
            "build_done",
            f"specialists={len(spec.specialists)} handoffs={len(spec.handoffs)} "
            f"src_bytes={len(source_code)}",
            data={
                "industry_code": classification.industry_code,
                "system_name": spec.name,
                "specialist_ids": [s.id for s in spec.specialists],
            },
        )

        return {
            "classification": classification,
            "spec": spec,
            "source_code": source_code,
            "system_slug": _slug(spec.name),
        }

    def deploy(
        self,
        result: dict[str, Any],
        output_root: Path,
    ) -> dict[str, Path]:
        """Write build() result to {output_root}/{slug}/ as deployable artifact.

        Files:
          main.py        the Composer-emitted Python module (entry: ENTRY_AGENT)
          spec.json      MultiAgentSpec serialized (for replay / introspection)
          manifest.json  build metadata (industry, classification, timestamps)

        Returns:
          dict mapping logical name to written Path.
        """
        if "source_code" not in result or "spec" not in result:
            raise ValueError(
                "deploy() expects build() result; got dict missing source_code/spec"
            )
        slug = result.get("system_slug") or _slug(result["spec"].name)
        target_dir = output_root / slug
        target_dir.mkdir(parents=True, exist_ok=True)

        main_py = target_dir / "main.py"
        spec_json = target_dir / "spec.json"
        manifest_json = target_dir / "manifest.json"

        main_py.write_text(result["source_code"], encoding="utf-8")
        spec_json.write_text(
            result["spec"].model_dump_json(indent=2), encoding="utf-8"
        )
        cls = result["classification"]
        manifest = {
            "system_slug": slug,
            "system_name": result["spec"].name,
            "industry": {
                "code": cls.industry_code,
                "primary": cls.primary,
                "sub": cls.sub,
                "business_scenario": cls.business_scenario,
            },
            "is_multi_agent": cls.is_multi_agent,
            "router_confidence": cls.confidence,
            "router_reasoning": cls.reasoning,
            "specialist_count": len(result["spec"].specialists),
            "handoff_count": len(result["spec"].handoffs),
            "guardrail_count": len(result["spec"].guardrails),
            "runtime": result["spec"].runtime,
            "built_at": datetime.now(timezone.utc).isoformat(),
            "built_by": "MultiAgentFactoryPipeline",
        }
        manifest_json.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        emit(
            "L6",
            "MultiAgentFactoryPipeline",
            "deploy_done",
            f"slug={slug} files=3",
            data={"target_dir": str(target_dir)},
        )

        return {"main": main_py, "spec": spec_json, "manifest": manifest_json}
