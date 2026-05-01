"""Ad-hoc browser diagnosis: open the harness UI, capture console logs + screenshot."""
from __future__ import annotations

import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path("/tmp/harness-diag")
OUT.mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()

    logs: list[str] = []
    errors: list[str] = []
    page.on("console", lambda m: logs.append(f"[{m.type}] {m.text}"))
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("requestfailed", lambda r: logs.append(f"[FAIL] {r.url} {r.failure}"))
    page.on("response", lambda r: logs.append(f"[{r.status}] {r.request.method} {r.url[r.url.find('/api/'):][:80] if '/api/' in r.url else r.url[-60:]}") if "/api/" in r.url or "8000" in r.url else None)

    print("=== navigate ===")
    page.goto("http://127.0.0.1:8000/", wait_until="domcontentloaded", timeout=15000)
    page.wait_for_timeout(3500)  # wait for JS boot + fonts

    # Take screenshot
    page.screenshot(path=str(OUT / "welcome.png"), full_page=True)
    print(f"screenshot → {OUT / 'welcome.png'}")

    # Inspect DOM
    h1_text = page.text_content("h1") or ""
    start_btn = page.query_selector("#btn-start")
    start_visible = start_btn.is_visible() if start_btn else False
    badge_env = (page.text_content("#badge-env") or "").strip()
    badge_health = (page.text_content("#badge-health") or "").strip()
    badge_model = (page.text_content("#badge-model") or "").strip()

    print(f"h1:            {h1_text!r}")
    print(f"badge env:     {badge_env!r}")
    print(f"badge health:  {badge_health!r}")
    print(f"badge model:   {badge_model!r}")
    print(f"start btn visible: {start_visible}")

    # Click start with sample SOP
    print("\n=== click start with sample SOP ===")
    page.fill("#sop-input", "做一个团队任务看板,首页显示任务列表")
    page.click("#btn-start")
    page.wait_for_timeout(20000)  # wait longer for SSE stream

    # Inspect harness internal state
    state_dump = page.evaluate("() => ({ state: STATE.dialogState, pending: STATE.pending, sid: STATE.sessionId, msgs: STATE.messages.length, phases: STATE.phases })")
    print(f"\n[STATE js dump]: {state_dump}")

    page.screenshot(path=str(OUT / "after-start.png"), full_page=True)
    print(f"screenshot → {OUT / 'after-start.png'}")

    # Inspect chat
    state_badge = (page.text_content("#state-badge") or "").strip()
    messages_html = page.inner_html("#messages")
    msg_count = len(page.query_selector_all("#messages > div"))
    trace_count = len(page.query_selector_all("#trace-list > div"))
    print(f"state badge:   {state_badge!r}")
    print(f"messages:      {msg_count} rows")
    print(f"trace lines:   {trace_count}")
    if msg_count == 0:
        print(f"\nmessages html (first 500):\n{messages_html[:500]}")

    print("\n=== Console logs ===")
    for l in logs[-30:]:
        print(f"  {l}")

    print("\n=== Page errors ===")
    for e in errors:
        print(f"  {e}")

    browser.close()
print("\nDone.")
