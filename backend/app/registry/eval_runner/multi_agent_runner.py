"""MultiAgentEvalRunnerImpl - Phase 7 V2.1.0 multi-agent path eval runner.

Mirrors EvalRunnerImpl but routes through MultiAgentFactoryPipeline. Honors
ExpectedShape multi-agent fields:
  classify_industry / classify_scenario / classify_is_multi_agent
  min_specialists / max_specialists / min_handoffs
  require_compose_ok
"""
from __future__ import annotations

import ast
import time as _time

from app.core.pipelines.factory.multi_agent_pipeline import (
    MultiAgentFactoryPipeline,
    MultiAgentNotApplicable,
)
from app.core.trace.bus import emit
from app.registry.eval_runner.models import (
    EvalCase,
    EvalCaseResult,
    EvalReport,
    EvalSet,
)


class MultiAgentEvalRunnerImpl:
    """Drives ES-002 (multi-agent eval) through MultiAgentFactoryPipeline."""

    def __init__(self, pipeline: MultiAgentFactoryPipeline | None = None) -> None:
        self._pipeline = pipeline or MultiAgentFactoryPipeline()

    async def run(self, eval_set: EvalSet) -> EvalReport:
        emit(
            "L4",
            "MultiAgentEvalRunner",
            "run_start",
            f"set={eval_set.asset_id} cases={len(eval_set.cases)}",
        )
        results: list[EvalCaseResult] = []
        for case in eval_set.cases:
            results.append(await self._run_one(case))

        passed = sum(1 for r in results if r.passed)
        failed = len(results) - passed
        pass_rate = passed / len(results) if results else 0.0

        emit(
            "L4",
            "MultiAgentEvalRunner",
            "run_done",
            f"set={eval_set.asset_id} passed={passed}/{len(results)} rate={pass_rate:.2%}",
        )
        return EvalReport(
            eval_set_id=eval_set.asset_id,
            total=len(results),
            passed=passed,
            failed=failed,
            pass_rate=pass_rate,
            case_results=results,
        )

    async def _run_one(self, case: EvalCase) -> EvalCaseResult:
        t0 = _time.time()
        exp = case.expected

        # Stage 1: classify
        try:
            classification = await self._pipeline._router.classify(case.nl)
        except Exception as exc:
            return EvalCaseResult(
                case_id=case.case_id,
                passed=False,
                reasons=[f"classify raised: {type(exc).__name__}: {exc}"],
                elapsed_ms=int((_time.time() - t0) * 1000),
                error=str(exc),
            )

        actual_industry = classification.industry_code
        actual_scenario = classification.business_scenario
        actual_is_multi = classification.is_multi_agent

        # Build via build_with_classification (skips re-classify)
        compose_ok: bool | None = None
        actual_specialists = None
        actual_handoffs = None
        try:
            result = await self._pipeline.build_with_classification(
                case.nl, classification
            )
            spec = result["spec"]
            actual_specialists = len(spec.specialists)
            actual_handoffs = len(spec.handoffs)
            # Verify compose output is parseable
            try:
                ast.parse(result["source_code"])
                compose_ok = True
            except SyntaxError:
                compose_ok = False
        except MultiAgentNotApplicable:
            # Router said single; if expectation also said single, that's OK;
            # otherwise mismatch.
            if exp.classify_is_multi_agent is False:
                # expected single -> treat as PASS for the classify stage,
                # spec/compose won't apply
                pass
            else:
                return EvalCaseResult(
                    case_id=case.case_id,
                    passed=False,
                    reasons=[
                        f"router classified is_multi_agent=False but case expects multi-agent"
                    ],
                    actual_industry=actual_industry,
                    actual_scenario=actual_scenario,
                    actual_is_multi_agent=actual_is_multi,
                    elapsed_ms=int((_time.time() - t0) * 1000),
                )
        except Exception as exc:
            return EvalCaseResult(
                case_id=case.case_id,
                passed=False,
                reasons=[f"build raised: {type(exc).__name__}: {exc}"],
                actual_industry=actual_industry,
                actual_scenario=actual_scenario,
                actual_is_multi_agent=actual_is_multi,
                elapsed_ms=int((_time.time() - t0) * 1000),
                error=str(exc),
            )

        # Now check expectations
        passed = True
        reasons: list[str] = []

        if exp.classify_industry is not None and actual_industry != exp.classify_industry:
            passed = False
            reasons.append(
                f"industry {actual_industry!r} != expected {exp.classify_industry!r}"
            )

        if exp.classify_scenario is not None:
            if not actual_scenario or exp.classify_scenario not in actual_scenario:
                passed = False
                reasons.append(
                    f"scenario {actual_scenario!r} does not contain expected substring "
                    f"{exp.classify_scenario!r}"
                )

        if exp.classify_is_multi_agent is not None:
            if actual_is_multi != exp.classify_is_multi_agent:
                passed = False
                reasons.append(
                    f"is_multi_agent {actual_is_multi} != expected {exp.classify_is_multi_agent}"
                )

        if actual_specialists is not None:
            if exp.min_specialists is not None and actual_specialists < exp.min_specialists:
                passed = False
                reasons.append(
                    f"specialists {actual_specialists} < min {exp.min_specialists}"
                )
            if exp.max_specialists is not None and actual_specialists > exp.max_specialists:
                passed = False
                reasons.append(
                    f"specialists {actual_specialists} > max {exp.max_specialists}"
                )

        if exp.min_handoffs is not None and actual_handoffs is not None:
            if actual_handoffs < exp.min_handoffs:
                passed = False
                reasons.append(
                    f"handoffs {actual_handoffs} < min {exp.min_handoffs}"
                )

        if exp.require_compose_ok and compose_ok is False:
            passed = False
            reasons.append("require_compose_ok=True but Composer output failed ast.parse")

        return EvalCaseResult(
            case_id=case.case_id,
            passed=passed,
            reasons=reasons,
            actual_industry=actual_industry,
            actual_scenario=actual_scenario,
            actual_is_multi_agent=actual_is_multi,
            actual_specialists=actual_specialists,
            actual_handoffs=actual_handoffs,
            compose_ok=compose_ok,
            elapsed_ms=int((_time.time() - t0) * 1000),
        )
