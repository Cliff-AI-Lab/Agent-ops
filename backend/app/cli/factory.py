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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="factory", description="Agent Ops V2.0.0 factory")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_build = sub.add_parser("build", help="NL -> Dify YAML")
    p_build.add_argument("nl", help="自然语言需求")
    p_build.add_argument("--output", "-o", default=None, help="写 DSL 到文件")
    p_build.add_argument("--json", action="store_true", help="机器可读输出")

    args = parser.parse_args(argv)

    if args.cmd != "build":
        parser.error(f"unknown command: {args.cmd}")
        return 2

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


if __name__ == "__main__":
    raise SystemExit(main())
