"""`factory` CLI - run the V2.0.0 factory pipeline locally.

Usage:
    factory build "<NL 需求>"
    factory build "..." --output workflow.yaml
    factory build "..." --json    # machine-readable output

Loads atoms from capabilities/atom/, runs IntentParser -> Resolver -> Compiler
-> Validator end-to-end. Requires SANDBOX_API_BASE + RUIDONG_API_KEY in env
unless --mock-llm is passed (for offline smoke tests).

Exit codes:
    0 - build succeeded, validator ok
    1 - LLM gateway unreachable / config missing
    2 - bad arguments
    3 - validator failed (build produced DSL but validation has errors)
    4 - factory raised before producing DSL
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass


def _atoms_dir() -> Path:
    """Locate capabilities/atom relative to this CLI's install."""
    custom = os.environ.get("FACTORY_ATOMS_DIR")
    if custom:
        return Path(custom)
    here = Path(__file__).resolve()
    repo_root = here.parents[3]
    return repo_root / "capabilities" / "atom"


async def _run_build(nl: str) -> dict[str, Any]:
    from app.core.pipelines.factory.pipeline import FactoryPipeline
    from app.core.pipelines.factory.validator import DSLValidatorImpl

    atoms_dir = _atoms_dir()
    if not atoms_dir.exists():
        raise FileNotFoundError(
            f"atoms dir missing: {atoms_dir} "
            f"(set FACTORY_ATOMS_DIR or run from agent-harness root)"
        )

    pipeline = FactoryPipeline(atoms_dir=atoms_dir)
    result = await pipeline.build(nl)

    # Validate each target output independently (hybrid emits both)
    validator = DSLValidatorImpl()
    validations: dict[str, dict] = {}
    for target_name, dsl_text in result.get("outputs", {}).items():
        report = await validator.validate(dsl_text, target_name)
        validations[target_name] = {
            "ok": report.ok,
            "issues": [i.model_dump() for i in report.issues],
        }
    overall_ok = all(v["ok"] for v in validations.values()) if validations else False
    result["validation"] = {
        "ok": overall_ok,
        "per_target": validations,
        # legacy: combined error issues
        "issues": [
            {**i, "target": t}
            for t, v in validations.items()
            for i in v["issues"]
            if i["severity"] == "error"
        ],
    }
    return result


def _human_print(result: dict[str, Any]) -> None:
    intent = result["intent"]
    dag = result["dag"]
    val = result["validation"]
    outputs = result.get("outputs", {})
    print(f"goal:    {intent.goal}")
    print(f"trigger: {intent.trigger.type} {intent.trigger.cron_expr or ''}")
    print(f"target:  {dag.target}")
    print(f"steps:   {len(intent.steps)}")
    print(f"nodes:   {len(dag.nodes)}")
    print(f"edges:   {len(dag.edges)}")
    print(f"issues:  {len(dag.issues)}")
    print(f"outputs: {', '.join(f'{t}={len(d)}c' for t, d in outputs.items())}")
    print(f"valid:   {'ok' if val['ok'] else 'FAIL'}")
    for target_name, per in val.get("per_target", {}).items():
        marker = "ok" if per["ok"] else "FAIL"
        err_count = sum(1 for i in per["issues"] if i["severity"] == "error")
        info_count = sum(1 for i in per["issues"] if i["severity"] == "info")
        print(f"  {target_name:6s}: {marker} (err={err_count}, info={info_count})")
    if dag.issues:
        print("dag issues:")
        for i in dag.issues:
            print(f"  - {i}")
    err = [i for i in val["issues"] if i["severity"] == "error"]
    if err:
        print("validation errors:")
        for i in err:
            print(f"  - [{i.get('target','-')}] {i.get('message')}")


def _machine_print(result: dict[str, Any]) -> None:
    print(
        json.dumps(
            {
                "goal": result["intent"].goal,
                "target": result["dag"].target,
                "node_count": len(result["dag"].nodes),
                "edge_count": len(result["dag"].edges),
                "asset_ids": [n.asset_id for n in result["dag"].nodes],
                "issues": result["dag"].issues,
                "validation": result["validation"],
                "outputs": result.get("outputs", {}),
                "dsl": result["dsl"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


async def _run_eval_multi(eval_set_id: str | None, only_case: str | None) -> Any:
    """V2.1+ multi-agent eval (ES-002 via MultiAgentEvalRunner)."""
    from app.core.pipelines.factory.multi_agent_pipeline import MultiAgentFactoryPipeline
    from app.registry.eval_runner import MultiAgentEvalRunnerImpl, load_eval_sets

    atoms_dir = _atoms_dir()
    eval_dir = atoms_dir.parent / "eval_set"
    if not eval_dir.exists():
        raise FileNotFoundError(f"eval_set dir missing: {eval_dir}")

    sets = load_eval_sets(eval_dir)
    target_id = eval_set_id or "ES-002"
    if target_id not in sets:
        raise KeyError(
            f"eval_set {target_id} not found; available: {list(sets)}"
        )
    es = sets[target_id]

    # P3: eval-multi only supports multi-agent eval sets.
    # Heuristic: at least one case must declare classify_is_multi_agent or
    # min_specialists; otherwise this set is for single-agent (use 'factory eval').
    is_multi_agent_set = any(
        c.expected.classify_is_multi_agent is not None
        or c.expected.min_specialists is not None
        for c in es.cases
    )
    if not is_multi_agent_set:
        raise ValueError(
            f"eval set {target_id!r} has no multi-agent expectations "
            f"(no classify_is_multi_agent / min_specialists in any case). "
            f"Use 'factory eval' for single-agent eval sets like ES-001."
        )

    if only_case:
        es = es.model_copy(
            update={"cases": [c for c in es.cases if c.case_id == only_case]}
        )
        if not es.cases:
            raise KeyError(f"case {only_case} not found in {es.asset_id}")

    pipeline = MultiAgentFactoryPipeline()
    runner = MultiAgentEvalRunnerImpl(pipeline=pipeline)
    return await runner.run(es)


def _human_eval_multi_print(report: Any) -> None:
    print(f"eval_set:  {report.eval_set_id}")
    print(f"total:     {report.total}")
    print(f"passed:    {report.passed}")
    print(f"failed:    {report.failed}")
    print(f"pass_rate: {report.pass_rate:.1%}")
    mvp = "OK" if report.is_mvp_threshold_met else "FAIL"
    ga = "OK" if report.is_ga_threshold_met else "FAIL"
    print(f"MVP gate (>= 70%): {mvp}")
    print(f"GA gate  (>= 80%): {ga}")
    print()
    for r in report.case_results:
        marker = "[PASS]" if r.passed else "[FAIL]"
        ind = r.actual_industry or "-"
        scn = r.actual_scenario or "-"
        spec_n = r.actual_specialists if r.actual_specialists is not None else "-"
        ho = r.actual_handoffs if r.actual_handoffs is not None else "-"
        compose = "ok" if r.compose_ok else ("fail" if r.compose_ok is False else "-")
        print(
            f"  {marker} {r.case_id:32s} ind={ind} scn={scn} "
            f"specs={spec_n} handoffs={ho} compose={compose} ({r.elapsed_ms} ms)"
        )
        if not r.passed:
            for reason in r.reasons:
                print(f"    -> {reason}")


async def _run_eval(eval_set_id: str | None, only_case: str | None) -> Any:
    from app.core.pipelines.factory.pipeline import FactoryPipeline
    from app.registry.eval_runner import EvalRunnerImpl, load_eval_sets

    atoms_dir = _atoms_dir()
    eval_dir = atoms_dir.parent / "eval_set"
    if not eval_dir.exists():
        raise FileNotFoundError(f"eval_set dir missing: {eval_dir}")

    sets = load_eval_sets(eval_dir)
    if eval_set_id:
        if eval_set_id not in sets:
            raise KeyError(
                f"eval_set {eval_set_id} not found; available: {list(sets)}"
            )
        es = sets[eval_set_id]
    else:
        es = next(iter(sets.values()))

    if only_case:
        es = es.model_copy(
            update={"cases": [c for c in es.cases if c.case_id == only_case]}
        )
        if not es.cases:
            raise KeyError(f"case {only_case} not found in {es.asset_id}")

    pipeline = FactoryPipeline(atoms_dir=atoms_dir)
    runner = EvalRunnerImpl(pipeline=pipeline)
    return await runner.run(es)


def _human_eval_print(report: Any) -> None:
    print(f"eval_set:  {report.eval_set_id}")
    print(f"total:     {report.total}")
    print(f"passed:    {report.passed}")
    print(f"failed:    {report.failed}")
    print(f"pass_rate: {report.pass_rate:.1%}")
    mvp = "OK" if report.is_mvp_threshold_met else "FAIL"
    ga = "OK" if report.is_ga_threshold_met else "FAIL"
    print(f"MVP gate (>= 70%): {mvp}")
    print(f"GA gate  (>= 80%): {ga}")
    print()
    for r in report.case_results:
        marker = "[PASS]" if r.passed else "[FAIL]"
        print(
            f"  {marker} {r.case_id:32s} target={r.actual_target or '-':6s} "
            f"nodes={r.actual_node_count} edges={r.actual_edge_count} "
            f"subs={','.join(r.actual_subcategories) or '-'} "
            f"({r.elapsed_ms} ms)"
        )
        if not r.passed:
            for reason in r.reasons:
                print(f"    -> {reason}")


async def _run_build_auto(
    nl: str, deploy_dir: Path | None, budget_cny: float | None = None
) -> dict[str, Any]:
    """V2.1.0+ Phase 7: auto-route NL between single-agent and multi-agent paths.

    V2.2: optional budget_cny activates pre-flight cost gate.
    """
    from app.core.pipelines.factory.pipeline import FactoryPipeline
    from app.core.pipelines.factory.switcher import FactorySwitcher

    atoms_dir = _atoms_dir()
    if not atoms_dir.exists():
        raise FileNotFoundError(
            f"atoms dir missing: {atoms_dir} "
            f"(set FACTORY_ATOMS_DIR or run from agent-harness root)"
        )

    estimator = None
    budget = None
    if budget_cny is not None:
        from app.core.governance import HeuristicCostEstimator, ThresholdCostBudget
        estimator = HeuristicCostEstimator()
        budget = ThresholdCostBudget(threshold_cny=budget_cny)

    single = FactoryPipeline(atoms_dir=atoms_dir)
    switcher = FactorySwitcher(
        single_pipeline=single,
        cost_estimator=estimator,
        cost_budget=budget,
    )
    result = await switcher.build(nl)

    if deploy_dir is not None and result["path"] == "multi":
        # Re-import here to avoid circular reference at module-load time
        from app.core.pipelines.factory.multi_agent_pipeline import (
            MultiAgentFactoryPipeline,
        )

        multi = MultiAgentFactoryPipeline()
        paths = multi.deploy(result, deploy_dir)
        result["deployed_files"] = {k: str(v) for k, v in paths.items()}
    return result


def _human_print_auto(result: dict[str, Any]) -> None:
    cls = result["classification"]
    path = result["path"]
    print(f"path:        {path}")
    print(
        f"industry:    {cls.industry_code} {cls.primary}"
        + (f" / {cls.sub}" if cls.sub else "")
    )
    print(f"scenario:    {cls.business_scenario or '-'}")
    print(f"confidence:  {cls.confidence:.2f}")
    print(f"reasoning:   {cls.reasoning}")
    print()

    if path == "multi":
        spec = result["spec"]
        print(f"system:      {spec.name}")
        print(f"specialists: {len(spec.specialists)}")
        print(f"handoffs:    {len(spec.handoffs)}")
        print(f"guardrails:  {len(spec.guardrails)}")
        print(f"slug:        {result['system_slug']}")
        print(f"src_bytes:   {len(result['source_code'])}")
        if "deployed_files" in result:
            print()
            print("deployed:")
            for k, v in result["deployed_files"].items():
                print(f"  {k}: {v}")
    else:
        # single: forward to existing _human_print(no validation key here yet)
        intent = result["intent"]
        dag = result["dag"]
        print(f"goal:        {intent.goal}")
        print(f"trigger:     {intent.trigger.type} {intent.trigger.cron_expr or ''}")
        print(f"target:      {dag.target}")
        print(f"nodes:       {len(dag.nodes)}")
        print(f"edges:       {len(dag.edges)}")
        outs = result.get("outputs", {})
        if outs:
            sizes = ", ".join(f"{k}={len(v)}c" for k, v in outs.items())
            print(f"outputs:     {sizes}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="factory", description="Agent Ops V2.0.0 factory")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_build = sub.add_parser("build", help="NL -> Dify YAML (single-agent path only)")
    p_build.add_argument("nl", help="自然语言需求")
    p_build.add_argument("--output", "-o", default=None, help="写 DSL 到文件")
    p_build.add_argument("--json", action="store_true", help="机器可读输出")

    p_eval = sub.add_parser("eval", help="跑评测集")
    p_eval.add_argument("--set", "-s", default=None, help="eval_set_id; 默认第一个")
    p_eval.add_argument("--only", default=None, help="只跑某一 case_id")
    p_eval.add_argument("--json", action="store_true", help="机器可读输出")

    p_auto = sub.add_parser(
        "build-auto",
        help="V2.1+ NL -> single OR multi-agent (auto-routed by IndustryRouter)",
    )
    p_auto.add_argument("nl", help="自然语言需求")
    p_auto.add_argument(
        "--deploy",
        default=None,
        help="部署到目录（多智能体路径）：写 main.py + spec.json + manifest.json",
    )
    p_auto.add_argument("--json", action="store_true", help="机器可读输出（不渲染 spec object）")
    p_auto.add_argument(
        "--budget-cny",
        type=float,
        default=None,
        help="V2.2 cost gate (CNY)：超过预估则拒绝 build（不传则不启用）",
    )

    p_eval_multi = sub.add_parser(
        "eval-multi",
        help="V2.1+ 跑多智能体评测集（ES-002）via MultiAgentEvalRunner",
    )
    p_eval_multi.add_argument("--set", "-s", default="ES-002", help="eval_set_id; 默认 ES-002")
    p_eval_multi.add_argument("--only", default=None, help="只跑某一 case_id")
    p_eval_multi.add_argument("--json", action="store_true", help="机器可读输出")

    args = parser.parse_args(argv)

    if args.cmd == "build":
        try:
            result = asyncio.run(_run_build(args.nl))
        except FileNotFoundError as exc:
            print(f"[factory] config error: {exc}", file=sys.stderr)
            return 1
        except Exception as exc:  # noqa: BLE001
            print(f"[factory] build failed: {exc}", file=sys.stderr)
            return 4

        if args.output:
            Path(args.output).write_text(result["dsl"], encoding="utf-8")
            print(f"[factory] wrote DSL -> {args.output}", file=sys.stderr)

        if args.json:
            _machine_print(result)
        else:
            _human_print(result)

        if not result["validation"]["ok"]:
            return 3
        return 0

    if args.cmd == "eval":
        try:
            report = asyncio.run(_run_eval(args.set, args.only))
        except (FileNotFoundError, KeyError) as exc:
            print(f"[factory] config error: {exc}", file=sys.stderr)
            return 1
        except Exception as exc:  # noqa: BLE001
            print(f"[factory] eval failed: {exc}", file=sys.stderr)
            return 4

        if args.json:
            print(report.model_dump_json(indent=2))
        else:
            _human_eval_print(report)

        if not report.is_mvp_threshold_met:
            return 3
        return 0

    if args.cmd == "eval-multi":
        try:
            report = asyncio.run(_run_eval_multi(args.set, args.only))
        except (FileNotFoundError, KeyError, ValueError) as exc:
            print(f"[factory] config error: {exc}", file=sys.stderr)
            return 1
        except Exception as exc:  # noqa: BLE001
            print(f"[factory] eval-multi failed: {exc}", file=sys.stderr)
            return 4
        if args.json:
            print(report.model_dump_json(indent=2))
        else:
            _human_eval_multi_print(report)
        # ES-002 is the V2.1.0 GA gate — must hit GA threshold (80%), not MVP (70%).
        # P1 fix per codex review 2026-05-05.
        if not report.is_ga_threshold_met:
            return 3
        return 0

    if args.cmd == "build-auto":
        deploy_dir = Path(args.deploy) if args.deploy else None
        try:
            result = asyncio.run(_run_build_auto(args.nl, deploy_dir, args.budget_cny))
        except FileNotFoundError as exc:
            print(f"[factory] config error: {exc}", file=sys.stderr)
            return 1
        except Exception as exc:  # noqa: BLE001
            from app.core.governance import CostBudgetExceeded
            if isinstance(exc, CostBudgetExceeded):
                print(f"[factory] budget exceeded: {exc.decision.reason}", file=sys.stderr)
                return 5
            print(f"[factory] build-auto failed: {exc}", file=sys.stderr)
            return 4

        if args.json:
            cls = result["classification"]
            payload: dict[str, Any] = {
                "path": result["path"],
                "classification": cls.model_dump(),
            }
            if result["path"] == "multi":
                payload["system_slug"] = result["system_slug"]
                payload["source_code"] = result["source_code"]
                payload["spec"] = result["spec"].model_dump()
                if "deployed_files" in result:
                    payload["deployed_files"] = result["deployed_files"]
            else:
                payload["goal"] = result["intent"].goal
                payload["target"] = result["dag"].target
                payload["node_count"] = len(result["dag"].nodes)
                payload["asset_ids"] = [n.asset_id for n in result["dag"].nodes]
                payload["outputs"] = result.get("outputs", {})
            print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        else:
            _human_print_auto(result)
        return 0

    parser.error(f"unknown command: {args.cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
