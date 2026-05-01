"""WorkflowEngine — DAG executor for WorkflowSpec (blueprint M3).

Strict, deterministic walk over a WorkflowSpec:

    1. Validate the spec topologically (cycles → fail fast).
    2. For each step in topological order:
         a. Resolve input_mapping ('inputs.X' / 'steps.<id>.<field>' / literal)
         b. Invoke the bound capability via CapabilityInvoker
         c. On failure apply step.on_failure (fail / retry / fallback / skip)
    3. Emit typed WorkflowEvent objects throughout (great for SSE).
    4. Produce a WorkflowRunResult bundle (per-step outputs + any errors).

The engine does **no planning**. The spec is the audit boundary
(blueprint §3.1 deterministic shell).
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.core.trace.bus import emit
from app.core.workflow_engine.invoker import CapabilityInvoker, InvocationResult
from app.ontology import WorkflowSpec, WorkflowStep

_LOG = logging.getLogger("agent_ops.workflow_engine.executor")


class StepResult(BaseModel):
    """Outcome of one step inside a workflow run."""

    step_id: str
    capability: str
    status: str  # "ok" | "failed" | "fallback" | "skipped" | "blocked_for_approval"
    attempts: int = 1
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    used_mock: bool = False
    elapsed_ms: int = 0

    model_config = ConfigDict(extra="forbid")


class WorkflowEvent(BaseModel):
    """SSE-friendly event emitted during a run."""

    kind: str  # "run_start" | "step_start" | "step_done" | "step_failed" | "run_done"
    workflow_id: str
    step_id: str | None = None
    message: str = ""
    data: dict[str, Any] | None = None

    model_config = ConfigDict(extra="forbid")


class WorkflowRunResult(BaseModel):
    """Final bundle returned by the engine after a complete run."""

    run_id: str
    workflow_id: str
    workflow_version: str
    status: str  # "succeeded" | "partial" | "failed"
    steps: list[StepResult]
    total_elapsed_ms: int
    inputs: dict[str, Any] = Field(default_factory=dict)
    final_outputs: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="step_id → output (mirrors steps[*].output for quick lookup).",
    )

    model_config = ConfigDict(extra="forbid")


class CycleError(Exception):
    """Raised when WorkflowSpec.steps form a cycle."""


def topological_order(spec: WorkflowSpec) -> list[WorkflowStep]:
    """Kahn's algorithm. Raises CycleError on cycles. Stable on tie-breaks."""
    by_id: dict[str, WorkflowStep] = {s.id: s for s in spec.steps}
    indeg: dict[str, int] = {s.id: 0 for s in spec.steps}
    children: dict[str, list[str]] = {s.id: [] for s in spec.steps}
    for s in spec.steps:
        for dep in s.depends_on:
            if dep not in by_id:
                # Dangling depends_on — treat as already satisfied (validator should
                # have caught this earlier; we don't fail the run here).
                continue
            indeg[s.id] += 1
            children[dep].append(s.id)

    ready: list[str] = sorted([sid for sid, d in indeg.items() if d == 0])
    out: list[WorkflowStep] = []
    while ready:
        sid = ready.pop(0)
        out.append(by_id[sid])
        for c in children[sid]:
            indeg[c] -= 1
            if indeg[c] == 0:
                ready.append(c)
        ready.sort()  # deterministic order

    if len(out) != len(spec.steps):
        unresolved = [s.id for s in spec.steps if s.id not in {x.id for x in out}]
        raise CycleError(f"workflow has a dependency cycle involving: {unresolved}")
    return out


def resolve_input_mapping(
    mapping: dict[str, str],
    inputs: dict[str, Any],
    step_outputs: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Resolve 'inputs.<name>' / 'steps.<id>.<field>' refs into concrete values."""
    resolved: dict[str, Any] = {}
    for k, ref in mapping.items():
        if not isinstance(ref, str):
            resolved[k] = ref
            continue
        if ref.startswith("inputs."):
            path = ref.split(".", 1)[1]
            resolved[k] = _walk(inputs, path)
        elif ref.startswith("steps."):
            parts = ref.split(".", 2)
            if len(parts) < 3:
                resolved[k] = None
                continue
            _, step_id, field_path = parts
            step_out = step_outputs.get(step_id, {})
            resolved[k] = _walk(step_out, field_path)
        else:
            # Literal value (string)
            resolved[k] = ref
    return resolved


def _walk(obj: Any, dotted: str) -> Any:
    cur: Any = obj
    for part in dotted.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


class WorkflowEngine:
    """Run a WorkflowSpec, yielding events and returning a typed result."""

    def __init__(self, invoker: CapabilityInvoker | None = None) -> None:
        self._invoker = invoker or CapabilityInvoker()

    async def run(
        self,
        spec: WorkflowSpec,
        inputs: dict[str, Any] | None = None,
    ) -> AsyncIterator[WorkflowEvent | WorkflowRunResult]:
        """Execute the spec. Yields WorkflowEvent for each step and the final WorkflowRunResult."""
        run_id = uuid.uuid4().hex[:12]
        inputs = dict(inputs or {})
        t0 = time.time()

        emit(
            "L3", "WorkflowEngine", "run_start",
            f"workflow={spec.workflow_id} v{spec.version} · steps={len(spec.steps)} · run_id={run_id}",
            data={"run_id": run_id, "step_count": len(spec.steps)},
        )
        yield WorkflowEvent(
            kind="run_start",
            workflow_id=spec.workflow_id,
            message=f"start · steps={len(spec.steps)} · run_id={run_id}",
            data={"run_id": run_id},
        )

        try:
            order = topological_order(spec)
        except CycleError as exc:
            emit("L3", "WorkflowEngine", "cycle_error", str(exc))
            yield WorkflowEvent(kind="run_done", workflow_id=spec.workflow_id,
                                message=f"abort: {exc}", data={"status": "failed"})
            yield WorkflowRunResult(
                run_id=run_id, workflow_id=spec.workflow_id,
                workflow_version=spec.version, status="failed", steps=[],
                total_elapsed_ms=int((time.time() - t0) * 1000), inputs=inputs,
            )
            return

        step_results: list[StepResult] = []
        step_outputs: dict[str, dict[str, Any]] = {}

        for step in order:
            # Skip if any predecessor failed AND that failure cascades
            if _any_blocking_predecessor(step, step_results):
                step_results.append(StepResult(
                    step_id=step.id, capability=step.capability,
                    status="skipped", attempts=0, output={},
                    error="upstream step failed", used_mock=False,
                ))
                yield WorkflowEvent(kind="step_failed", workflow_id=spec.workflow_id,
                                    step_id=step.id, message="skipped (upstream failed)")
                continue

            yield WorkflowEvent(
                kind="step_start", workflow_id=spec.workflow_id,
                step_id=step.id,
                message=f"step '{step.id}' → {step.capability}",
                data={"capability": step.capability},
            )

            result = await self._run_one_step(step, inputs, step_outputs)
            step_results.append(result)
            if result.status in {"ok", "fallback"}:
                step_outputs[step.id] = result.output
                yield WorkflowEvent(
                    kind="step_done", workflow_id=spec.workflow_id, step_id=step.id,
                    message=f"✓ {step.id} ({result.elapsed_ms}ms · {'mock' if result.used_mock else 'real'})",
                    data={"output_keys": list(result.output.keys()),
                          "used_mock": result.used_mock,
                          "attempts": result.attempts},
                )
            else:
                yield WorkflowEvent(
                    kind="step_failed", workflow_id=spec.workflow_id, step_id=step.id,
                    message=f"✗ {step.id} · {result.error}",
                    data={"status": result.status, "error": result.error,
                          "attempts": result.attempts},
                )

        # Aggregate status
        statuses = {r.status for r in step_results}
        if not step_results:
            final_status = "failed"
        elif statuses <= {"ok", "fallback"}:
            final_status = "succeeded"
        elif "ok" in statuses or "fallback" in statuses:
            final_status = "partial"
        else:
            final_status = "failed"

        elapsed = int((time.time() - t0) * 1000)
        result_bundle = WorkflowRunResult(
            run_id=run_id, workflow_id=spec.workflow_id,
            workflow_version=spec.version, status=final_status, steps=step_results,
            total_elapsed_ms=elapsed, inputs=inputs, final_outputs=step_outputs,
        )

        emit(
            "L3", "WorkflowEngine", "run_done",
            f"workflow={spec.workflow_id} · status={final_status} · steps={len(step_results)} · {elapsed}ms",
            data={"status": final_status, "elapsed_ms": elapsed, "run_id": run_id},
        )
        yield WorkflowEvent(
            kind="run_done", workflow_id=spec.workflow_id,
            message=f"{final_status} · {elapsed}ms",
            data=result_bundle.model_dump(),
        )
        yield result_bundle

    # ---- Internal ----
    async def _run_one_step(
        self,
        step: WorkflowStep,
        inputs: dict[str, Any],
        step_outputs: dict[str, dict[str, Any]],
    ) -> StepResult:
        """Execute one step with on_failure semantics + retries."""
        # Approval gates: blueprint §3.6 first-class human approval. v0 surfaces
        # the gate but doesn't block; Batch G will integrate a real approval bus.
        if step.approval_required:
            emit(
                "L3", "WorkflowEngine", "approval_required",
                f"step '{step.id}' requires approval (auto-pass in v0)",
            )

        attempts_left = max(1, 1 + (step.max_retries or 0))
        attempts_used = 0
        last_error: str | None = None
        used_mock = False
        elapsed_total = 0

        while attempts_left > 0:
            attempts_used += 1
            attempts_left -= 1
            try:
                resolved_inputs = resolve_input_mapping(
                    step.input_mapping, inputs, step_outputs
                )
            except Exception as exc:  # noqa: BLE001
                last_error = f"input mapping: {type(exc).__name__}: {exc}"
                continue

            # Optional timeout
            if step.timeout_ms:
                try:
                    invocation = await asyncio.wait_for(
                        self._invoker.invoke(step.capability, resolved_inputs),
                        timeout=step.timeout_ms / 1000,
                    )
                except asyncio.TimeoutError:
                    last_error = f"timeout > {step.timeout_ms}ms"
                    elapsed_total += step.timeout_ms
                    if attempts_left > 0:
                        continue
                    break
            else:
                invocation = await self._invoker.invoke(step.capability, resolved_inputs)

            elapsed_total += invocation.elapsed_ms
            used_mock = used_mock or invocation.used_mock

            if invocation.error is None:
                return StepResult(
                    step_id=step.id, capability=step.capability,
                    status="ok", attempts=attempts_used, output=invocation.output,
                    error=None, used_mock=invocation.used_mock,
                    elapsed_ms=elapsed_total,
                )

            last_error = invocation.error
            if attempts_left == 0:
                break

        # All retries exhausted — apply on_failure
        action = step.on_failure
        if action == "skip":
            return StepResult(
                step_id=step.id, capability=step.capability, status="skipped",
                attempts=attempts_used, output={}, error=last_error,
                used_mock=used_mock, elapsed_ms=elapsed_total,
            )
        if action == "fallback" and step.fallback_step:
            return StepResult(
                step_id=step.id, capability=step.capability, status="fallback",
                attempts=attempts_used, output={"__fallback_to__": step.fallback_step},
                error=last_error, used_mock=used_mock, elapsed_ms=elapsed_total,
            )
        if action == "block_for_approval":
            return StepResult(
                step_id=step.id, capability=step.capability,
                status="blocked_for_approval", attempts=attempts_used, output={},
                error=last_error, used_mock=used_mock, elapsed_ms=elapsed_total,
            )
        # Default: fail
        return StepResult(
            step_id=step.id, capability=step.capability, status="failed",
            attempts=attempts_used, output={}, error=last_error,
            used_mock=used_mock, elapsed_ms=elapsed_total,
        )


def _any_blocking_predecessor(step: WorkflowStep, prior: list[StepResult]) -> bool:
    """Return True if any predecessor of ``step`` did not produce usable output."""
    by_id = {r.step_id: r for r in prior}
    for dep in step.depends_on:
        r = by_id.get(dep)
        if r is None:
            continue
        if r.status not in {"ok", "fallback"}:
            return True
    return False


__all__ = [
    "WorkflowEngine",
    "WorkflowEvent",
    "WorkflowRunResult",
    "StepResult",
    "topological_order",
    "resolve_input_mapping",
    "CycleError",
]
