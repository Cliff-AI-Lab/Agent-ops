"""Batch G — focused validation that the self-reinforcing loop closes.

Skips Phase 2/3 (the slow part of the full E2E test) and re-uses the previously
generated `gen.team-weekly-reporter-*` agent. Steps:

    1. Load the existing GeneratedAsset row from `harness.db`
    2. Re-extract intent_keywords via real Claude Sonnet 4.6 + new prompt
    3. Rewrite the agent.yaml on disk + update the DB row
    4. Promote (status='active') and reload AgentStore
    5. Run IntentRouter against a related SOP ("工时统计周报")
    6. Assert `gen.{asset_id}` appears in candidate_shortlist

Total wall-clock: ~30s (one light LLM call + one IntentRouter run).
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Make the backend package importable
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import yaml  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]


def hr(title: str) -> None:
    print()
    print("─" * 70)
    print(title)
    print("─" * 70)


async def main() -> int:
    from app.config import get_settings
    from app.core.llm.client import LLMClient
    from app.core.orchestrator.intent_router import IntentRouter
    from app.marketplace.asset_store import (
        bootstrap_asset_store,
        default_generated_agents_dir,
        get_asset_store,
    )
    from app.marketplace.auto_register import reload_generated_agents
    from app.marketplace.keyword_extractor import extract_intent_keywords
    from app.ontology import RequirementSpec
    from app.registry import agent_store as ags
    from app.registry import store as cs
    from app.registry import tool_store as ts
    from app.registry.bootstrap import bootstrap_registry

    cs.reset_store()
    ts.reset_tool_store()
    ags.reset_agent_store()
    bootstrap_registry()
    ts.bootstrap_tools()
    ags.bootstrap_agents()
    bootstrap_asset_store()

    model = get_settings().harness_default_model
    if not model:
        print("✗ HARNESS_DEFAULT_MODEL not configured — abort")
        return 1

    # ---------------------------------------------------------------- step 1
    hr("STEP 1 · 找到现有的 generated asset")
    store = get_asset_store()
    drafts = store.list()
    target = next((a for a in drafts if a.asset_id.startswith("team-weekly-reporter-")), None)
    if target is None:
        print("✗ 没找到 team-weekly-reporter-* asset。先跑一次 verify_e2e_real.py 制造一个。")
        return 1
    print(f"  asset_id  = {target.asset_id}")
    print(f"  status    = {target.status}")
    print(f"  原 keywords = {target.intent_keywords}")

    # ---------------------------------------------------------------- step 2
    hr("STEP 2 · 用 Claude Sonnet 4.6 真调 extract_intent_keywords")
    spec = RequirementSpec(
        product_name="Team Weekly Reporter",
        product_type="tool",
        target_users=["5-10 人研发小组"],
        core_pages=["周报列表", "周报详情"],
        special_requirements=[
            "每周一早上自动触发生成",
            "从 GitHub 拉每个人的 commit 数、PR 数、关键 commit message",
            "LLM 总结个人亮点与风险",
            "输出到共享空间",
        ],
    )
    sop = (
        "做一个团队周报生成的小工具,可以从 GitHub 拉每个人的 commit "
        "然后用 LLM 总结成周报"
    )
    skip_extract = "--skip-extract" in sys.argv
    if skip_extract:
        # Reuse whatever is in the agent.yaml (faster local re-validation)
        contract = yaml.safe_load(
            (default_generated_agents_dir() / target.asset_id / "agent.yaml")
            .read_text(encoding="utf-8")
        )
        new_keywords = list(contract.get("intent_keywords", []))
        print(f"  (--skip-extract) reusing {len(new_keywords)} keywords from disk")
    else:
        llm = LLMClient()
        try:
            new_keywords = await extract_intent_keywords(
                llm, model=model, spec=spec, source_sop=sop,
            )
        finally:
            await llm.aclose()
    print(f"  keywords ({len(new_keywords)}):")
    for kw in new_keywords:
        print(f"    - {kw}")

    # ---------------------------------------------------------------- step 3
    hr("STEP 3 · 覆盖 agent.yaml + 更新 DB row")
    agent_dir = default_generated_agents_dir() / target.asset_id
    yaml_path = agent_dir / "agent.yaml"
    contract = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    contract["intent_keywords"] = new_keywords
    yaml_path.write_text(
        yaml.safe_dump(contract, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    print(f"  ✓ rewrote {yaml_path}")
    target.intent_keywords = new_keywords
    store.register(target)
    print("  ✓ DB row updated")

    # ---------------------------------------------------------------- step 4
    hr("STEP 4 · 促销 + reload")
    store.promote(target.asset_id, promoted_by="batch-g-validation")
    n = reload_generated_agents()
    new_agent_id = f"gen.{target.asset_id}"
    print(f"  reloaded {n} promoted agents into AgentStore")
    print(f"  new agent in store: {ags.get_agent_store().get(new_agent_id) is not None}")

    # ---------------------------------------------------------------- step 5
    hr("STEP 5 · IntentRouter 跑相关 SOP")
    sop2 = "我也想做一个开发团队的工时统计周报,每周自动生成总结"
    print(f"  sop2 = {sop2}")
    llm = LLMClient()
    try:
        ir = IntentRouter(llm, light_model=model, heavy_model=model)
        result = await ir.plan(sop2)
    finally:
        await llm.aclose()

    shortlist_ids = [c.id for c in result.candidate_shortlist]
    print(f"  intent: {result.intent_summary}")
    print(f"  shortlist ({len(shortlist_ids)}):")
    for cid in shortlist_ids:
        star = "  ⭐ NEW" if cid == new_agent_id else ""
        print(f"    - {cid}{star}")
    print(f"  spec_steps: {[(s.id, s.capability) for s in result.workflow_spec.steps]}")
    print(f"  spec_valid: {result.spec_valid}")

    hr("结论")
    closed = new_agent_id in shortlist_ids
    print(f"  自我强化循环 closed: {'YES ✅' if closed else 'NO ⚠'}")
    print(f"  expected agent: {new_agent_id}")
    if not closed:
        print("  原因: 关键词召回未匹配。看上面 keywords 是否包含足够的'周报/工时'相关概念。")
    return 0 if closed else 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
