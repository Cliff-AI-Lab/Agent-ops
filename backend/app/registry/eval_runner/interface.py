"""EvalRunner Protocol."""

from __future__ import annotations

from typing import Protocol

from app.registry.eval_runner.models import EvalReport, EvalSet


class EvalRunner(Protocol):
    """Run an EvalSet through factory pipeline, return EvalReport."""

    async def run(self, eval_set: EvalSet) -> EvalReport: ...
