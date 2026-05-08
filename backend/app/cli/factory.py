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


def _default_harness_db_path() -> Path:
    env = os.getenv("HARNESS_DB_PATH")
    if env:
        return Path(env)
    # backend/app/cli/factory.py -> harness root = parents[3]
    return Path(__file__).resolve().parents[3] / "harness.db"


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
    nl: str,
    deploy_dir: Path | None,
    budget_cny: float | None = None,
    tenant_id: str = "default",
    use_tiktoken: bool = True,
    soft_warn: bool = True,
) -> dict[str, Any]:
    """V2.1.0+ Phase 7: auto-route NL between single-agent and multi-agent paths.

    V2.2: optional budget_cny activates pre-flight cost gate.
    V2.3: tiktoken real token counting + 80% soft warning + tenant_id partition.
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
        from app.core.governance import (
            HeuristicCostEstimator,
            SoftWarnBudget,
            ThresholdCostBudget,
            TiktokenCostEstimator,
        )
        estimator = TiktokenCostEstimator() if use_tiktoken else HeuristicCostEstimator()
        underlying = ThresholdCostBudget(threshold_cny=budget_cny)
        budget = SoftWarnBudget(underlying=underlying) if soft_warn else underlying

    single = FactoryPipeline(atoms_dir=atoms_dir)
    switcher = FactorySwitcher(
        single_pipeline=single,
        cost_estimator=estimator,
        cost_budget=budget,
        tenant_id=tenant_id,
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
    p_auto.add_argument(
        "--tenant",
        type=str,
        default="default",
        help="V2.3 多租户 tenant_id（cost ledger 分区键）",
    )
    p_auto.add_argument(
        "--no-tiktoken",
        action="store_true",
        help="V2.3 关闭 tiktoken 真 token 计数，回退到字符长度代理（V2.2 行为）",
    )
    p_auto.add_argument(
        "--no-soft-warn",
        action="store_true",
        help="V2.3 关闭 80% 软警告（仍硬封顶）",
    )

    p_deps = sub.add_parser(
        "deps", help="V2.4 影响面分析：查 asset_id 的依赖与反向依赖"
    )
    p_deps.add_argument("asset_id", help="asset_id (atom.x.y.v1 / prompt.x.y.v1 / ...)")
    p_deps.add_argument("--json", action="store_true", help="机器可读输出")

    p_eval_multi = sub.add_parser(
        "eval-multi",
        help="V2.1+ 跑多智能体评测集（ES-002）via MultiAgentEvalRunner",
    )
    p_eval_multi.add_argument("--set", "-s", default="ES-002", help="eval_set_id; 默认 ES-002")
    p_eval_multi.add_argument("--only", default=None, help="只跑某一 case_id")
    p_eval_multi.add_argument("--json", action="store_true", help="机器可读输出")

    p_deploy = sub.add_parser(
        "deploy",
        help="V2.6 一键发布: NL 或 YAML -> Dify 真机 (黑灯工厂)",
    )
    g_in = p_deploy.add_mutually_exclusive_group(required=True)
    g_in.add_argument("--nl", help="自然语言需求 (走 build 再 deploy)")
    g_in.add_argument("--yaml", help="已有 Dify YAML 文件路径 (跳过 build)")
    p_deploy.add_argument(
        "--specimen-id",
        required=True,
        help="生产单 ID. 同 specimen_id 复用同 dify_app_id (覆盖升级)",
    )
    p_deploy.add_argument("--target", default="dify", choices=["dify"],
                          help="部署目标. n8n/hybrid 留 Phase 9")
    p_deploy.add_argument("--dify-base", default=None,
                          help="Dify base URL; 默认 $DIFY_BASE_URL 或 http://localhost:8080")
    p_deploy.add_argument("--name", default=None, help="Dify app name override")
    p_deploy.add_argument("--json", action="store_true", help="机器可读输出")

    p_drift = sub.add_parser(
        "drift-check",
        help="V2.6 检查 Dify 上的工厂部署是否被人改过 (单向 push 边界)",
    )
    p_drift.add_argument("--specimen-id", required=True, help="生产单 ID")
    p_drift.add_argument("--dify-base", default=None,
                         help="Dify base URL; 默认 $DIFY_BASE_URL 或 http://localhost:8080")
    p_drift.add_argument("--json", action="store_true", help="机器可读输出")

    p_score = sub.add_parser(
        "atom-score",
        help="V2.7 看某个原子的使用度评分 (黑灯工厂自反馈)",
    )
    p_score.add_argument("asset_id", help="atom asset_id, e.g. atom.llm.chat.v1")
    p_score.add_argument("--db", default=None, help="harness.db 路径; 默认仓库根")
    p_score.add_argument("--json", action="store_true", help="机器可读输出")

    p_rank = sub.add_parser(
        "atom-rank",
        help="V2.7 列出原子使用度排行 (高到低)",
    )
    p_rank.add_argument("--layer", default="atom.",
                        help="layer prefix; 默认 'atom.'")
    p_rank.add_argument("--top", type=int, default=None, help="只看前 N 个")
    p_rank.add_argument("--db", default=None, help="harness.db 路径; 默认仓库根")
    p_rank.add_argument("--json", action="store_true", help="机器可读输出")

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

    if args.cmd == "deps":
        from app.registry.atom_loader import AtomLoaderImpl
        from app.registry.prompt_loader import PromptLoaderImpl
        from app.registry.dependency_graph import build_graph_from_atoms_and_prompts

        atoms_dir = _atoms_dir()
        prompts_dir = atoms_dir.parent / "prompts"
        atoms = AtomLoaderImpl().load_all(atoms_dir) if atoms_dir.exists() else {}
        prompts = PromptLoaderImpl().load_all(prompts_dir) if prompts_dir.exists() else {}
        g = build_graph_from_atoms_and_prompts(atoms, prompts)
        report = g.impact_of(args.asset_id)
        depends = g.depends_on(args.asset_id)

        if args.json:
            print(json.dumps({
                "asset_id": args.asset_id,
                "depends_on": depends,
                "direct_dependents": report.direct_dependents,
                "all_dependents": report.all_dependents,
                "total_assets_in_graph": len(g.all_assets()),
            }, ensure_ascii=False, indent=2))
        else:
            print(f"asset:                {args.asset_id}")
            print(f"depends on (out):     {depends or '-'}")
            print(f"direct dependents:    {report.direct_dependents or '-'}")
            print(f"all dependents (BFS): {report.all_dependents or '-'}")
            print(f"graph size:           {len(g.all_assets())} assets")
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
            result = asyncio.run(_run_build_auto(
                args.nl,
                deploy_dir,
                args.budget_cny,
                tenant_id=args.tenant,
                use_tiktoken=not args.no_tiktoken,
                soft_warn=not args.no_soft_warn,
            ))
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

    if args.cmd == "deploy":
        from dataclasses import asdict
        from app.delivery.dify_publisher import DifyPublisher

        if args.yaml:
            yaml_path = Path(args.yaml)
            if not yaml_path.exists():
                print(f"[factory] yaml not found: {yaml_path}", file=sys.stderr)
                return 1
            yaml_text = yaml_path.read_text(encoding="utf-8")
            print(f"[deploy] using existing yaml: {yaml_path} ({len(yaml_text)} chars)",
                  file=sys.stderr)
        else:
            try:
                build_result = asyncio.run(_run_build(args.nl))
            except Exception as exc:  # noqa: BLE001
                print(f"[factory] build failed before deploy: {exc}", file=sys.stderr)
                return 4
            outputs = build_result.get("outputs", {})
            yaml_text = outputs.get("dify") or build_result.get("dsl") or ""
            if not yaml_text:
                print("[factory] build produced no dify YAML", file=sys.stderr)
                return 4
            print(f"[deploy] built {len(yaml_text)} chars from NL", file=sys.stderr)

        publisher = DifyPublisher(base_url=args.dify_base)
        try:
            res = publisher.publish(args.specimen_id, yaml_text, app_name=args.name)
        except RuntimeError as exc:
            print(f"[factory] deploy failed: {exc}", file=sys.stderr)
            return 4

        if args.json:
            print(json.dumps(asdict(res), ensure_ascii=False, indent=2))
        else:
            tag = "FIRST" if res.is_first_deploy else f"REDEPLOY (count={res.deploy_count})"
            print(f"[deploy] {tag} specimen={res.specimen_id}")
            print(f"  dify_app_id: {res.dify_app_id}")
            print(f"  status:      {res.status}")
            print(f"  yaml sha256: {res.yaml_sha256}")
            if res.error:
                print(f"  ERROR:       {res.error}")
            else:
                base = (args.dify_base or os.getenv("DIFY_BASE_URL") or "http://localhost:8080").rstrip("/")
                print(f"  view in Dify: {base}/app/{res.dify_app_id}/workflow")

        return 0 if not res.error else 4

    if args.cmd == "drift-check":
        from dataclasses import asdict
        from app.delivery.dify_publisher import DifyPublisher

        publisher = DifyPublisher(base_url=args.dify_base)
        try:
            report = publisher.drift_check(args.specimen_id)
        except RuntimeError as exc:
            print(f"[factory] drift-check failed: {exc}", file=sys.stderr)
            return 4

        if args.json:
            print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
        else:
            print(f"[drift-check] specimen={report.specimen_id}")
            print(f"  dify_app_id:    {report.dify_app_id or '(none)'}")
            print(f"  factory sha256: {report.factory_sha256 or '-'}")
            print(f"  dify sha256:    {report.dify_sha256 or '-'}")
            print(f"  drift:          {'YES' if report.has_drift else 'no'}")
            print(f"  reason:         {report.reason}")
            if report.detail:
                print(f"  detail:         {report.detail}")

        return 3 if report.has_drift else 0

    if args.cmd in ("atom-score", "atom-rank"):
        from dataclasses import asdict
        from app.delivery.atom_score_service import AtomScoreService
        from app.registry.atom_loader import AtomLoaderImpl

        atoms_dir = _atoms_dir()
        atoms = AtomLoaderImpl().load_all(atoms_dir) if atoms_dir.exists() else {}

        db_arg = getattr(args, "db", None)
        db_path = Path(db_arg) if db_arg else _default_harness_db_path()
        if not db_path.exists():
            print(f"[factory] harness.db not found: {db_path}", file=sys.stderr)
            return 1
        svc = AtomScoreService.from_atom_loader(db_path, atoms)

        if args.cmd == "atom-score":
            score = svc.score(args.asset_id)
            if args.json:
                payload = asdict(score)
                if score.stats:
                    payload["stats"] = asdict(score.stats)
                print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
            else:
                print(f"atom_id:    {score.atom_id}")
                print(f"score:      {score.score:.4f}")
                print(f"history:    {'yes' if score.has_history else 'no (cold start)'}")
                print(f"static pr:  {score.static_pass_rate}")
                if score.stats:
                    s = score.stats
                    print(f"specimens:  {s.specimen_count}")
                    print(f"qa pass/fail: {s.qa_pass}/{s.qa_fail}")
                    print(f"industries: {s.industry_breadth}")
                    print(f"last used:  {s.last_used_at or '-'}")
                print(f"explain:    {score.explanation}")
                if score.components:
                    print("components:")
                    for k, v in score.components.items():
                        print(f"  {k}: {v}")
            return 0

        # atom-rank
        ranked = svc.rank(layer_prefix=args.layer, top=args.top)
        if args.json:
            print(json.dumps(
                [{"atom_id": r.atom_id, "score": r.score,
                  "has_history": r.has_history,
                  "specimens": (r.stats.specimen_count if r.stats else 0)}
                 for r in ranked],
                ensure_ascii=False, indent=2,
            ))
        else:
            print(f"{'rank':>4} {'score':>7} {'hist':>4} {'spec':>4}  atom_id")
            for i, r in enumerate(ranked, 1):
                hist = "yes" if r.has_history else "no"
                spc = r.stats.specimen_count if r.stats else 0
                print(f"{i:>4}  {r.score:>6.4f}  {hist:>4} {spc:>4}  {r.atom_id}")
        return 0

    parser.error(f"unknown command: {args.cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
