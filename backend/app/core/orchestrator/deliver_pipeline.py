from __future__ import annotations

from collections.abc import AsyncIterator

from pydantic import BaseModel, ConfigDict

from app.core.dialog.session import Session
from app.core.llm.client import LLMClient
from app.core.orchestrator.blueprint_planner import BlueprintPlanner
from app.core.pipelines.ui.orchestrator import UIPipeline
from app.core.stability.contracts import CodeArtifact, RequirementSpec
from app.core.trace.bus import emit
from app.registry.preset_injector import inject_presets


class DeliverEvent(BaseModel):
    """SSE-friendly event emitted by the unified deliver pipeline."""

    # "plan_start" | "plan_done" | "phase2" | "phase3" | "preset_inject" | "completed"
    stage: str
    message: str = ""
    data: dict[str, object] | None = None

    model_config = ConfigDict(extra="forbid")


class DeliverPipeline:
    """End-to-end orchestration: requirement_spec → blueprint → prototypes → code."""

    def __init__(self, llm: LLMClient, model: str) -> None:
        self._llm = llm
        self._model = model

    async def run(self, session: Session) -> AsyncIterator[DeliverEvent]:
        """Run the full deliver pipeline, yielding SSE events for each stage."""
        if not session.requirement_spec:
            raise ValueError("session has no requirement_spec")
        spec = RequirementSpec.model_validate(session.requirement_spec)

        emit("L3", "DeliverPipeline", "start",
             f"session={session.id[:8]} · product={spec.product_name} · model={self._model}",
             session_id=session.id)

        # Stage 1 — L3 BlueprintPlanner
        yield DeliverEvent(
            stage="plan_start",
            message=f"L3 planning blueprint · {len(spec.core_pages)} pages",
        )
        planner = BlueprintPlanner(self._llm, model=self._model)
        plan_result = await planner.plan(spec)
        blueprint = plan_result.value
        yield DeliverEvent(
            stage="plan_done",
            message=(
                f"✓ blueprint ready · pages={len(blueprint.pages)} · "
                f"attempts={plan_result.attempts} · fallback={plan_result.used_fallback}"
            ),
            data={
                "blueprint": blueprint.model_dump(),
                "attempts": plan_result.attempts,
                "fallback_used": plan_result.used_fallback,
            },
        )

        # Stage 2 — L5 UIPipeline (Phase2 + Phase3). For non-completed events
        # we forward as-is. On the "completed" event we intercept the artifact,
        # run PresetInjector, then emit the final completed with the augmented
        # artifact so the client sees the project with preset files merged in.
        pipeline = UIPipeline(self._llm, model=self._model)
        async for ui_event in pipeline.run(blueprint):
            if ui_event.phase != "completed":
                yield DeliverEvent(
                    stage=ui_event.phase,
                    message=ui_event.message,
                    data=ui_event.data,
                )
                continue

            # Completed: intercept and inject presets
            data = dict(ui_event.data or {})
            code_dump = data.get("code")
            if isinstance(code_dump, dict):
                try:
                    artifact = CodeArtifact.model_validate(code_dump)
                except Exception as exc:  # noqa: BLE001
                    emit("L6", "PresetInjector", "skip",
                         f"could not validate artifact: {exc}",
                         session_id=session.id)
                    yield DeliverEvent(stage="completed", message=ui_event.message, data=data)
                    continue

                yield DeliverEvent(
                    stage="preset_inject",
                    message=f"L6 preset injector · product_type={spec.product_type}",
                    data=None,
                )
                emit("L6", "PresetInjector", "start",
                     f"product_type={spec.product_type} · file_count={len(artifact.files)}",
                     session_id=session.id)
                injection = inject_presets(artifact, spec, blueprint)
                emit("L6", "PresetInjector", "done",
                     f"injected={len(injection.injected_bundles)} · "
                     f"skipped={len(injection.skipped)} · "
                     f"new_file_count={len(injection.artifact.files)}",
                     data={
                         "injected": injection.injected_bundles,
                         "skipped": [{"bundle_id": b, "reason": r} for b, r in injection.skipped],
                     },
                     session_id=session.id)

                # Replace the artifact in the completed payload
                data["code"] = injection.artifact.model_dump()
                data["preset_injection"] = {
                    "injected": injection.injected_bundles,
                    "skipped": [{"bundle_id": b, "reason": r} for b, r in injection.skipped],
                }

                # Batch X+G — auto-register the artifact into the Asset Hub.
                # Before persisting, ask a light LLM for 5-10 business-concept
                # keywords so future related SOPs can find this generated agent.
                try:
                    from app.marketplace.auto_register import auto_register
                    from app.marketplace.keyword_extractor import extract_intent_keywords

                    first_user_msg = next(
                        (m.content for m in session.messages if m.role == "user"),
                        spec.product_name,
                    )
                    intent_keywords = await extract_intent_keywords(
                        self._llm,
                        model=self._model,
                        spec=spec,
                        source_sop=first_user_msg,
                    )
                    asset = auto_register(
                        injection.artifact,
                        spec,
                        source_session_id=session.id,
                        source_sop=first_user_msg,
                        intent_keywords=intent_keywords,
                    )
                    data["asset"] = asset.model_dump(mode="json")
                    yield DeliverEvent(
                        stage="asset_registered",
                        message=f"L6 Marketplace · asset_id={asset.asset_id} · status=draft",
                        data={
                            "asset_id": asset.asset_id,
                            "status": asset.status,
                            "intent_keywords": intent_keywords,
                        },
                    )
                except Exception as exc:  # noqa: BLE001
                    emit("L6", "Marketplace", "auto_register_failed",
                         f"{type(exc).__name__}: {exc}",
                         session_id=session.id)

            yield DeliverEvent(
                stage="completed",
                message=ui_event.message,
                data=data,
            )

        emit("L3", "DeliverPipeline", "completed",
             f"session={session.id[:8]} · all stages done",
             session_id=session.id)
