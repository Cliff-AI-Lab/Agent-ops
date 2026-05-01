"""Batch Z++ — quick real-LLM smoke test for the TriageAgent.

Runs three messages against a live Claude Sonnet 4.6 and prints the routing decisions.
Total wall-clock: ~10s.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]


async def main() -> int:
    from app.config import get_settings
    from app.core.llm.client import LLMClient
    from app.core.orchestrator.triage import TriageAgent
    from app.marketplace.asset_store import bootstrap_asset_store
    from app.marketplace.auto_register import reload_generated_agents
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
    reload_generated_agents()

    model = get_settings().harness_default_model
    if not model:
        print("✗ HARNESS_DEFAULT_MODEL not configured")
        return 1

    cases = [
        ("我想做一个发票识别小工具", None),
        ("Agent Ops 平台是干啥的?用法说明给我看一下。", None),
        ("用之前那个团队周报工具帮我生成本周周报", None),
    ]

    llm = LLMClient()
    try:
        triage = TriageAgent(llm, model=model)
        for msg, summary in cases:
            print()
            print("─" * 70)
            print(f"  msg: {msg}")
            result = await triage.route(msg, session_summary=summary)
            d = result.decision
            print(f"  → target          : {d.target.value}")
            print(f"  → target_id       : {d.target_id or '-'}")
            print(f"  → reason          : {d.reason}")
            print(f"  → forwarded       : {d.forwarded_message[:120]}")
            print(f"  → fallback_used   : {result.fallback_used}")
            print(f"  → attempts        : {result.attempts}")
            print(f"  → candidates seen : {result.candidate_agent_ids}")
    finally:
        await llm.aclose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
