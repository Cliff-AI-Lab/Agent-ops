"""End-to-end real-data verification of the Agent Ops self-reinforcing loop.

No mocks. Drives the live service with real Claude Sonnet 4.6 calls:

    1. POST /api/sessions           — open a session with an SOP
    2. POST /api/sessions/{id}/turn × 3   — multi-turn clarification until
       Receptionist auto-fills RequirementSpec (Receptionist switches to
       'confirming' on the 3rd user turn, blueprint §13)
    3. GET  /api/sessions/{id}      — confirm requirement_spec is populated
    4. POST /api/sessions/{id}/deliver  (SSE)  — full DeliverPipeline:
       BlueprintPlanner → UIPipeline (Phase 2 + Phase 3) → PresetInjector
       → auto_register into Marketplace + AssetHub
       Captures the auto-emitted ``asset_registered`` event
    5. GET  /api/assets/{asset_id}      — confirm draft row + zip on disk
    6. POST /api/assets/{asset_id}/promote      — flip to active + reload
    7. GET  /api/wiki/agents            — confirm gen.{asset_id} appears
    8. POST /api/intent/plan            — re-route a related SOP, confirm
       the just-promoted agent is in the candidate_shortlist (today's
       agent → tomorrow's atom)

The script runs serially and prints rich progress so you can watch it.
Full run is ~5–10 min (Phase 2 ≈ 2 min, Phase 3 ≈ 1.5 min, plus IntentRouter
calls). All Trace events are also visible in the browser /api/trace/stream.
"""
from __future__ import annotations

import json
import sys
import time

import httpx

sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

BASE = "http://127.0.0.1:8000"


def hr(title: str) -> None:
    print()
    print("─" * 70)
    print(title)
    print("─" * 70)


def stamp() -> str:
    return time.strftime("%H:%M:%S")


def stream_sse(client: httpx.Client, path: str, body: dict, *, timeout: float = 600) -> list[tuple[str, dict]]:
    """POST a request and consume the SSE response. Returns list of (event, data)."""
    events: list[tuple[str, dict]] = []
    with client.stream("POST", f"{BASE}{path}", json=body, timeout=timeout,
                       headers={"Accept": "text/event-stream"}) as r:
        r.raise_for_status()
        buf = ""
        for chunk in r.iter_text():
            buf += chunk
            while "\n\n" in buf or "\r\n\r\n" in buf:
                # split on blank line (handle both unix and windows line endings)
                idx_a = buf.find("\n\n")
                idx_b = buf.find("\r\n\r\n")
                idx = min(i for i in (idx_a, idx_b) if i >= 0) if (idx_a >= 0 or idx_b >= 0) else -1
                if idx < 0:
                    break
                frame, buf = buf[:idx], buf[idx + (4 if buf[idx:idx+4] == "\r\n\r\n" else 2):]
                event = None
                data = None
                for line in frame.split("\n"):
                    line = line.rstrip("\r")
                    if line.startswith("event:"):
                        event = line[6:].strip()
                    elif line.startswith("data:"):
                        data = line[5:].strip()
                if data is not None:
                    try:
                        parsed = json.loads(data)
                    except Exception:
                        parsed = {"raw": data}
                    events.append((event or "message", parsed))
                    print(f"  [{stamp()}] event={event or 'message'} {('· '+str(parsed.get('message',''))[:90]) if isinstance(parsed, dict) and parsed.get('message') else ''}")
    return events


def main():
    overall_start = time.time()
    sop = "做一个团队周报生成的小工具,可以从 GitHub 拉每个人的 commit 然后用 LLM 总结成周报"

    with httpx.Client() as c:
        # ------------------------------------------------------------------
        hr("STEP 1 · 创建 session")
        r = c.post(f"{BASE}/api/sessions", json={"sop": sop}, timeout=30)
        r.raise_for_status()
        sess = r.json()
        sid = sess["session_id"]
        print(f"  session_id: {sid}")
        print(f"  initial state: {sess['state']}")
        print(f"  sop: {sop}")

        # ------------------------------------------------------------------
        hr("STEP 2 · 真实 receptionist 多轮对话(每轮真调 Claude Sonnet 4.6)")
        # Three user turns to push user_turns to >=3 → Receptionist fills RequirementSpec
        replies = [
            "目标用户是 5-10 人的研发小组,周报每周一早上自动生成,放在共享空间。",
            "需要看每个人的 commit 数、PR 数、关键 commit message,我希望 LLM 总结一下亮点和风险。",
            "还需要一个简单的列表页查看历史周报,可以点开看完整文本。",
        ]
        for i, msg in enumerate(replies, 1):
            print(f"\n  turn {i} | user: {msg}")
            t0 = time.time()
            evs = stream_sse(c, f"/api/sessions/{sid}/turn", {"message": msg})
            elapsed = time.time() - t0
            states = [e[1].get("value") for e in evs if e[1].get("type") == "state"]
            tokens = "".join(e[1].get("value", "") for e in evs if e[1].get("type") == "token")
            print(f"  → states: {states} · tokens={len(tokens)} chars · {elapsed:.1f}s")
            print(f"    receptionist: {tokens[:200]}…")

        # ------------------------------------------------------------------
        hr("STEP 3 · 验证 RequirementSpec 已填充")
        r = c.get(f"{BASE}/api/sessions/{sid}", timeout=30)
        snap = r.json()
        spec = snap.get("requirement_spec")
        if not spec:
            print("  ⚠ requirement_spec 还没填好,可能需要更多对话。提前终止。")
            return
        print(f"  state: {snap['state']}")
        print(f"  product_name: {spec.get('product_name')}")
        print(f"  product_type: {spec.get('product_type')}")
        print(f"  target_users: {spec.get('target_users')}")
        print(f"  core_pages: {spec.get('core_pages')}")
        print(f"  special_requirements: {spec.get('special_requirements', [])[:3]}")

        # ------------------------------------------------------------------
        hr("STEP 4 · 跑 DeliverPipeline(真实 Claude Sonnet 4.6 · 预计 3-5 分钟)")
        print("  阶段 SSE 事件流:")
        t_deliver = time.time()
        events = stream_sse(c, f"/api/sessions/{sid}/deliver", {}, timeout=900)
        print(f"\n  total deliver elapsed: {time.time() - t_deliver:.1f}s")

        # Find the asset_registered event
        asset_event = next((e for e in events if e[0] == "asset_registered"), None)
        completed = next((e for e in events if e[0] == "completed"), None)
        if asset_event:
            asset_id = asset_event[1].get("asset_id")
            print(f"  ✅ auto_registered: asset_id = {asset_id}")
        elif completed and isinstance(completed[1].get("data"), dict) and completed[1]["data"].get("asset"):
            asset_id = completed[1]["data"]["asset"]["asset_id"]
            print(f"  ✅ asset_id (from completed.data.asset): {asset_id}")
        else:
            print("  ⚠ 没看到 asset_registered 事件,提前终止")
            return

        # ------------------------------------------------------------------
        hr(f"STEP 5 · 验证资产中心 GET /api/assets/{asset_id}")
        r = c.get(f"{BASE}/api/assets/{asset_id}", timeout=30)
        r.raise_for_status()
        a = r.json()
        print(f"  asset_id: {a['asset_id']}")
        print(f"  status: {a['status']}")
        print(f"  artifact_path: {a['artifact_path']}")
        print(f"  agent_yaml_path: {a['agent_yaml_path']}")
        print(f"  file_count: {a['file_count']} · total_bytes: {a['total_bytes']}")

        from pathlib import Path
        zip_path = Path(__file__).resolve().parents[1] / a["artifact_path"]
        yaml_path = Path(__file__).resolve().parents[1] / a["agent_yaml_path"] if a.get("agent_yaml_path") else None
        print(f"  zip exists on disk: {zip_path.is_file()} · {zip_path.stat().st_size if zip_path.exists() else 'N/A'} bytes")
        print(f"  agent.yaml exists: {yaml_path.is_file() if yaml_path else False}")

        # ------------------------------------------------------------------
        hr(f"STEP 6 · 晋升 POST /api/assets/{asset_id}/promote")
        r = c.post(f"{BASE}/api/assets/{asset_id}/promote", json={"promoted_by": "verify-e2e"}, timeout=30)
        r.raise_for_status()
        promoted = r.json()
        print(f"  status after promote: {promoted['asset']['status']}")
        print(f"  promoted_by: {promoted['asset']['promoted_by']}")
        print(f"  active in registry: {promoted['active_in_registry']} agent(s) reloaded")

        # ------------------------------------------------------------------
        hr("STEP 7 · 验证新 agent 进入 RegistryHub")
        r = c.get(f"{BASE}/api/wiki/stats", timeout=10)
        print(f"  /api/wiki/stats: {r.json()}")
        r = c.get(f"{BASE}/api/wiki/agents", timeout=10)
        agents = r.json()
        new_agent_id = f"gen.{asset_id}"
        in_wiki = any(a["agent_id"] == new_agent_id for a in agents.get("agents", []))
        print(f"  agents in wiki: {len(agents.get('agents', []))}")
        for a in agents.get("agents", []):
            mark = "  ⭐ NEW" if a["agent_id"] == new_agent_id else ""
            print(f"    - {a['agent_id']:<48} is_generated={a['is_generated']}{mark}")
        print(f"  新 agent 已进 RegistryHub: {in_wiki}")

        # ------------------------------------------------------------------
        hr("STEP 8 · 自我强化循环 — 用相关 SOP 再跑 IntentRouter")
        sop2 = "我也想做一个开发团队的工时统计周报,每周自动生成总结"
        print(f"  sop2: {sop2}")
        t_router = time.time()
        r = c.post(f"{BASE}/api/intent/plan", json={"sop": sop2}, timeout=180)
        r.raise_for_status()
        p2 = r.json()
        print(f"  → planner elapsed: {time.time() - t_router:.1f}s")
        print(f"  intent: {p2['intent_summary']}")
        shortlist = [c["id"] for c in p2["candidate_shortlist"]]
        print(f"  shortlist ({len(shortlist)}):")
        for sid_c in shortlist:
            star = "  ⭐ NEW" if sid_c == new_agent_id else ""
            print(f"    - {sid_c}{star}")
        spec_steps = [(s["id"], s["capability"]) for s in p2["workflow_spec"]["steps"]]
        print(f"  spec steps: {spec_steps}")
        print(f"  spec_valid: {p2['spec_valid']}")

        # ------------------------------------------------------------------
        hr("✅ 总结")
        loop_proven = new_agent_id in shortlist
        print(f"  自我强化循环成立: {'YES ✅' if loop_proven else 'NO  ⚠'}")
        if not loop_proven:
            print("  原因可能:retrieval 关键词不匹配,Batch G eval 改进点。")
        print(f"  total wall-clock: {time.time() - overall_start:.1f}s")


if __name__ == "__main__":
    main()
