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

    validator = DSLValidatorImpl()
    report = await validator.validate(result["dsl"], result["target"])
    result["validation"] = {
        "ok": report.ok,
        "issues": [i.model_dump() for i in report.issues],
    }
    return result


def _human_print(result: dict[str, Any]) -> None:
    intent = result["intent"]
    dag = result["dag"]
    val = result["validation"]
    print(f"goal:    {intent.goal}")
    print(f"trigger: {intent.trigger.type} {intent.trigger.cron_expr or ''}")
    print(f"target:  {dag.target}")
    print(f"steps:   {len(intent.steps)}")
    print(f"nodes:   {len(dag.nodes)}")
    print(f"edges:   {len(dag.edges)}")
    print(f"issues:  {len(dag.issues)}")
    print(f"valid:   {'ok' if val['ok'] else 'FAIL'}")
    if dag.issues:
        print("dag issues:")
        for i in dag.issues:
            print(f"  - {i}")
    err = [i for i in val["issues"] if i["severity"] == "error"]
    if err:
        print("validation errors:")
        for i in err:
            print(f"  - {i.get('message')}")


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
                "dsl": result["dsl"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="factory", description="Agent Ops V2.0.0 factory")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_build = sub.add_parser("build", help="NL -> Dify YAML")
    p_build.add_argument("nl", help="自然语言需求")
    p_build.add_argument("--output", "-o", default=None, help="写 DSL 到文件")
    p_build.add_argument("--json", action="store_true", help="机器可读输出")

    p_eval = sub.add_parser("eval", help="跑评测集")
    p_eval.add_argument("--set", "-s", default=None, help="eval_set_id; 默认第一个")
    p_eval.add_argument("--only", default=None, help="只跑某一 case_id")
    p_eval.add_argument("--json", action="store_true", help="机器可读输出")

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

    parser.error(f"unknown command: {args.cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
