"""EvalRunnerImpl - runs each case through FactoryPipeline + DSLValidator."""

from __future__ import annotations

import time as _time

from app.core.pipelines.factory.pipeline import FactoryPipeline
from app.core.pipelines.factory.validator import DSLValidatorImpl
from app.core.trace.bus import emit
from app.registry.eval_runner.models import (
    EvalCase,
    EvalCaseResult,
    EvalReport,
    EvalSet,
)


class EvalRunnerImpl:
    def __init__(
        self,
        pipeline: FactoryPipeline,
        validator: DSLValidatorImpl | None = None,
    ) -> None:
        self._pipeline = pipeline
        self._validator = validator or DSLValidatorImpl()

    async def run(self, eval_set: EvalSet) -> EvalReport:
        emit(
            "L4",
            "EvalRunner",
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
            "EvalRunner",
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
        try:
            result = await self._pipeline.build(case.nl)
        except Exception as exc:
            return EvalCaseResult(
                case_id=case.case_id,
                passed=False,
                reasons=[f"build raised: {type(exc).__name__}: {exc}"],
                elapsed_ms=int((_time.time() - t0) * 1000),
                error=str(exc),
            )

        dag = result["dag"]
        atoms_by_id = {n.asset_id: n for n in dag.nodes}
        actual_subs = self._collect_subcategories(dag, atoms_by_id)

        # Multi-target aware validation: hybrid emits both dify+n8n; validate
        # each output against its own target schema. Case passes if no errors
        # across all emitted outputs.
        outputs = result.get("outputs", {}) or {result["target"]: result["dsl"]}
        per_target_reports = {}
        for target_name, dsl_text in outputs.items():
            per_target_reports[target_name] = await self._validator.validate(
                dsl_text, target_name
            )
        # Synthesize a single ValidationReport-like view for downstream checks
        all_issues = [
            i for r in per_target_reports.values() for i in r.issues
        ]
        all_ok = all(r.ok for r in per_target_reports.values())

        # Backward-compat shape used by the issue-checking branch below
        from app.core.pipelines.factory.validator.interface import ValidationReport

        report = ValidationReport(
            ok=all_ok,
            target=result["target"],
            issues=all_issues,
        )

        passed = True
        reasons: list[str] = []
        exp = case.expected

        if exp.target and dag.target != exp.target:
            passed = False
            reasons.append(f"target {dag.target!r} != expected {exp.target!r}")

        if exp.subcategories:
            missing = [s for s in exp.subcategories if s not in actual_subs]
            if missing:
                passed = False
                reasons.append(
                    f"missing subcategories: {missing}; actual: {sorted(actual_subs)}"
                )

        if exp.min_nodes is not None and len(dag.nodes) < exp.min_nodes:
            passed = False
            reasons.append(
                f"node_count {len(dag.nodes)} < min {exp.min_nodes}"
            )
        if exp.max_nodes is not None and len(dag.nodes) > exp.max_nodes:
            passed = False
            reasons.append(
                f"node_count {len(dag.nodes)} > max {exp.max_nodes}"
            )
        if exp.min_edges is not None and len(dag.edges) < exp.min_edges:
            passed = False
            reasons.append(
                f"edge_count {len(dag.edges)} < min {exp.min_edges}"
            )

        if exp.require_validator_ok:
            errors = [i for i in report.issues if i.severity == "error"]
            if errors:
                passed = False
                reasons.append(
                    f"validator errors: {[e.message for e in errors][:3]}"
                )

        unexpected_issue_kinds = [
            i.get("kind")
            for i in dag.issues
            if i.get("kind") not in exp.allowed_issue_kinds
            and i.get("kind") != "low_confidence"  # warning, not blocker
        ]
        if unexpected_issue_kinds:
            passed = False
            reasons.append(f"unexpected issue kinds: {unexpected_issue_kinds}")

        return EvalCaseResult(
            case_id=case.case_id,
            passed=passed,
            reasons=reasons if not passed else ["all checks passed"],
            actual_target=dag.target,
            actual_subcategories=sorted(actual_subs),
            actual_node_count=len(dag.nodes),
            actual_edge_count=len(dag.edges),
            actual_issues=list(dag.issues),
            elapsed_ms=int((_time.time() - t0) * 1000),
        )

    def _collect_subcategories(self, dag, atoms_by_id) -> set[str]:
        """Look up subcategory of each atom referenced in the DAG.

        Resolver must have stored atoms in pipeline's search engine; we walk
        nodes and consult the search engine corpus for subcategory.
        """
        atoms = self._pipeline._resolver._search._atoms
        out: set[str] = set()
        for node in dag.nodes:
            atom = atoms.get(node.asset_id)
            if atom is not None:
                out.add(atom.subcategory)
        return out
