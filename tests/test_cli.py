"""ops CLI tests (Batch H)."""
from __future__ import annotations

import io
from contextlib import redirect_stderr, redirect_stdout

import httpx
import pytest
import respx

from app.cli.ops import main


@pytest.fixture(autouse=True)
def _set_base(monkeypatch):
    monkeypatch.setenv("OPS_BASE", "http://localhost:9999")
    yield


def _run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = main(argv)
    return rc, out.getvalue(), err.getvalue()


# ---------- version ----------
def test_version_prints_server_version():
    with respx.mock(base_url="http://localhost:9999") as router:
        router.get("/openapi.json").mock(return_value=httpx.Response(200, json={
            "info": {"title": "Agent Ops", "version": "0.5.0"},
        }))
        rc, out, _ = _run(["version"])
    assert rc == 0
    assert "Agent Ops" in out
    assert "0.5.0" in out


# ---------- health ----------
def test_health_prints_status():
    with respx.mock(base_url="http://localhost:9999") as router:
        router.get("/health").mock(return_value=httpx.Response(200, json={
            "status": "ok", "env": "dev",
        }))
        rc, out, _ = _run(["health"])
    assert rc == 0
    assert "ok" in out
    assert "dev" in out


# ---------- triage ----------
def test_triage_pretty_output():
    with respx.mock(base_url="http://localhost:9999") as router:
        router.post("/api/triage").mock(return_value=httpx.Response(200, json={
            "user_message": "hi",
            "decision": {
                "target": "help",
                "target_id": None,
                "reason": "meta question",
                "forwarded_message": "info",
            },
            "candidate_agent_ids": [],
            "fallback_used": False,
            "attempts": 1,
        }))
        rc, out, _ = _run(["triage", "hi"])
    assert rc == 0
    assert "target          : help" in out
    assert "meta question" in out


def test_triage_json_flag_dumps_payload():
    with respx.mock(base_url="http://localhost:9999") as router:
        router.post("/api/triage").mock(return_value=httpx.Response(200, json={"x": 1}))
        rc, out, _ = _run(["--json", "triage", "hello"])
    assert rc == 0
    assert '"x": 1' in out


# ---------- wiki search ----------
def test_wiki_search_lists_entries():
    with respx.mock(base_url="http://localhost:9999") as router:
        router.get("/api/wiki/search", params={"q": "周报"}).mock(
            return_value=httpx.Response(200, json={
                "results": [
                    {"kind": "agent", "id": "gen.weekly-1", "name": "Weekly"},
                    {"kind": "atom",  "id": "analysis.report.docanalyze", "name": "Doc Analyzer"},
                ],
            }),
        )
        rc, out, _ = _run(["wiki", "search", "周报"])
    assert rc == 0
    assert "gen.weekly-1" in out
    assert "analysis.report.docanalyze" in out


def test_wiki_search_handles_tools_and_capabilities_shape():
    """The current /api/wiki/search returns {tools, capabilities, total}; merge them."""
    with respx.mock(base_url="http://localhost:9999") as router:
        router.get("/api/wiki/search").mock(
            return_value=httpx.Response(200, json={
                "tools": [{"tool_id": "baidu.ocr.x", "name": "OCR"}],
                "capabilities": [{"capability_id": "ui.plan_blueprint", "name": "Planner"}],
                "total": 2,
            }),
        )
        rc, out, _ = _run(["wiki", "search", "x"])
    assert rc == 0
    assert "baidu.ocr.x" in out
    assert "ui.plan_blueprint" in out
    assert "atom" in out and "composite" in out


def test_wiki_search_no_matches():
    with respx.mock(base_url="http://localhost:9999") as router:
        router.get("/api/wiki/search").mock(return_value=httpx.Response(200, json={"results": []}))
        rc, out, _ = _run(["wiki", "search", "totally-unrelated"])
    assert rc == 0
    assert "(no matches)" in out


# ---------- asset ----------
def test_asset_list_pretty():
    with respx.mock(base_url="http://localhost:9999") as router:
        router.get("/api/assets").mock(return_value=httpx.Response(200, json={
            "assets": [
                {"asset_id": "weekly-1", "status": "active", "product_type": "tool", "name": "Weekly"},
            ],
        }))
        rc, out, _ = _run(["asset", "list"])
    assert rc == 0
    assert "weekly-1" in out
    assert "active" in out


def test_asset_list_with_status_filter():
    with respx.mock(base_url="http://localhost:9999") as router:
        router.get("/api/assets", params={"status": "draft"}).mock(
            return_value=httpx.Response(200, json={"assets": []}),
        )
        rc, out, _ = _run(["asset", "list", "--status", "draft"])
    assert rc == 0
    assert "(no assets)" in out


def test_asset_promote_pretty():
    with respx.mock(base_url="http://localhost:9999") as router:
        router.post("/api/assets/x123/promote").mock(return_value=httpx.Response(200, json={
            "asset": {"asset_id": "x123", "status": "active"},
            "active_in_registry": 3,
        }))
        rc, out, _ = _run(["asset", "promote", "x123"])
    assert rc == 0
    assert "✓ promoted x123" in out
    assert "active_in_registry: 3" in out


# ---------- error paths ----------
def test_network_error_exits_1():
    with respx.mock(base_url="http://localhost:9999") as router:
        router.get("/health").mock(side_effect=httpx.ConnectError("offline"))
        rc, _, err = _run(["health"])
    assert rc == 1
    assert "network error" in err.lower()


def test_4xx_exits_3():
    with respx.mock(base_url="http://localhost:9999") as router:
        router.get("/api/assets").mock(return_value=httpx.Response(404, text="not found"))
        rc, _, err = _run(["asset", "list"])
    assert rc == 3


def test_5xx_exits_1():
    with respx.mock(base_url="http://localhost:9999") as router:
        router.get("/health").mock(return_value=httpx.Response(503, text="down"))
        rc, _, err = _run(["health"])
    assert rc == 1


def test_bad_args_exits_2():
    """argparse errors land on exit code 2."""
    with pytest.raises(SystemExit) as excinfo:
        main(["asset", "list", "--status", "WRONG-VALUE"])
    assert excinfo.value.code == 2


# ---------- --base override ----------
def test_base_override_changes_url():
    with respx.mock(base_url="http://other.host:7777") as router:
        router.get("/health").mock(return_value=httpx.Response(200, json={"status": "ok", "env": "test"}))
        rc, out, _ = _run(["--base", "http://other.host:7777", "health"])
    assert rc == 0
    assert "ok" in out
