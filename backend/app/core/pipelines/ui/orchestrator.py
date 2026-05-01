from __future__ import annotations

from collections.abc import AsyncIterator

from pydantic import BaseModel, ConfigDict

from app.core.llm.client import LLMClient
from app.core.pipelines.ui.phase2_prototype import (
    Phase2Output,
    PrototypeVariant,
    generate_prototypes,
)
from app.core.pipelines.ui.phase3_production import generate_production_code
from app.core.stability.contracts import CodeArtifact, UIBlueprint
from app.core.trace.bus import emit


class UIPipelineEvent(BaseModel):
    """An SSE-friendly event emitted by the UI pipeline."""

    phase: str
    message: str = ""
    data: dict[str, object] | None = None

    model_config = ConfigDict(extra="forbid")


class UIPipelineResult(BaseModel):
    """The final aggregate result returned by the UI pipeline."""

    prototypes: Phase2Output
    chosen_variant: PrototypeVariant
    code: CodeArtifact
    fallback_used: bool

    model_config = ConfigDict(extra="forbid")


class UIPipeline:
    """Orchestrates prototype generation and production code generation."""

    def __init__(self, llm: LLMClient, model: str) -> None:
        self._llm = llm
        self._model = model

    async def run(self, blueprint: UIBlueprint) -> AsyncIterator[UIPipelineEvent]:
        """Orchestrate Phase2 -> pick variant[0] (MVP) -> Phase3."""
        emit("L5", "UIPipeline", "start",
             f"pipeline start · model={self._model} · pages={len(blueprint.pages)}")
        emit("L5", "Phase2", "start",
             f"generate 3 HTML prototype variants")
        phase2_result = await generate_prototypes(self._llm, model=self._model, blueprint=blueprint)
        prototypes = phase2_result.value
        chosen_variant = prototypes.variants[0]
        emit("L5", "Phase2", "done",
             f"✓ {len(prototypes.variants)} variants · attempts={phase2_result.attempts} · fallback={phase2_result.used_fallback} · chosen={chosen_variant.name}")
        yield UIPipelineEvent(
            phase="phase2",
            message="Generated HTML prototype variants.",
            data={
                "attempts": phase2_result.attempts,
                "fallback_used": phase2_result.used_fallback,
                "prototypes": prototypes.model_dump(),
                "chosen_variant": chosen_variant.model_dump(),
            },
        )

        emit("L5", "Phase3", "start",
             f"generate Vite + React + shadcn/ui production code")
        phase3_result = await generate_production_code(
            self._llm,
            model=self._model,
            blueprint=blueprint,
            chosen_variant=chosen_variant,
        )
        code = phase3_result.value
        emit("L5", "Phase3", "done",
             f"✓ {len(code.files)} files · attempts={phase3_result.attempts} · fallback={phase3_result.used_fallback}")
        yield UIPipelineEvent(
            phase="phase3",
            message="Generated production code artifact.",
            data={
                "attempts": phase3_result.attempts,
                "fallback_used": phase3_result.used_fallback,
                "entrypoint": code.entrypoint,
            },
        )

        pipeline_result = UIPipelineResult(
            prototypes=prototypes,
            chosen_variant=chosen_variant,
            code=code,
            fallback_used=phase2_result.used_fallback or phase3_result.used_fallback,
        )
        emit("L5", "UIPipeline", "completed",
             f"✓ pipeline complete · fallback_used={pipeline_result.fallback_used}")
        yield UIPipelineEvent(
            phase="completed",
            message="UI pipeline completed.",
            data=pipeline_result.model_dump(),
        )
