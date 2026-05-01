"""Integration tests for /api/factory/* HTTP endpoints (Phase 2 W5)."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.llm.client import ChatMessage


REPO_ROOT = Path(__file__).resolve().parents[1]
ATOMS_DIR = REPO_ROOT / "capabilities" / "atom"


def _intent_payload() -> dict:
    return {
        "schema_version": "1.0",
        "goal": "test",
        "trigger": {"type": "cron", "cron_expr": "0 9 * * 1"},
        "steps": [
            {
                "id": "s1",
                "verb": "查询数据库",
                "expected_output_kind": "tabular_data",
                "suggested_subcategory": "DB",
            }
        ],
        "outputs": [{"name": "out", "type": "void"}],
        "raw_user_input": "test",
    }


def _rank_payload() -> dict:
    return {
        "asset_id": "atom.db.postgres.v1",
        "confidence": 0.9,
        "reason": "mocked",
    }


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Use isolated harness.db per test, in tmp_path cwd."""
    monkeypatch.chdir(tmp_path)
    from app.db.init_db import init_db

    asyncio.run(init_db(db_path="harness.db"))
    return tmp_path


@pytest.fixture
def mocked_llm():
    """Patch LLMClient construction in pipeline + intent_parser to return a mock."""
    llm = MagicMock()
    llm.chat = AsyncMock(
        side_effect=[
            ChatMessage(role="assistant", content=json.dumps(_intent_payload())),
            ChatMessage(role="assistant", content=json.dumps(_rank_payload())),
        ]
        * 10  # extras for retries / multiple sessions
    )
    return llm


@pytest.fixture
def client(temp_db, mocked_llm):
    """FastAPI TestClient with LLMClient mocked + atoms_dir from repo."""
    with patch(
        "app.core.pipelines.factory.pipeline.LLMClient", return_value=mocked_llm
    ), patch(
        "app.core.pipelines.factory.intent_parser.impl.LLMClient",
        return_value=mocked_llm,
    ), patch(
        "app.core.llm.client.LLMClient.__init__", return_value=None
    ):
        # Import app AFTER patching so its routes wire to mocked components
        from app.main import app

        yield TestClient(app)


def test_start_returns_session_id(client):
    if not ATOMS_DIR.exists():
        pytest.skip("atoms dir missing")
    resp = client.post("/api/factory/start", json={"nl": "测试 NL 工厂启动"})
    assert resp.status_code == 200
    body = resp.json()
    assert "session_id" in body
    assert body["state"] in ("CREATED", "DESIGNING", "GATE_DESIGN")


def test_get_session_404_for_unknown(client):
    resp = client.get("/api/factory/nonexistent_session_id")
    assert resp.status_code == 404


def test_gate_404_for_unknown_session(client):
    resp = client.post(
        "/api/factory/nonexistent/gate",
        json={"decision": "pass"},
    )
    assert resp.status_code == 404


def test_gate_invalid_decision(client):
    if not ATOMS_DIR.exists():
        pytest.skip("atoms dir missing")
    start = client.post("/api/factory/start", json={"nl": "测试 NL"})
    sid = start.json()["session_id"]
    # Wait briefly for background task to advance to GATE_DESIGN
    time.sleep(2.0)
    resp = client.post(f"/api/factory/{sid}/gate", json={"decision": "warp"})
    assert resp.status_code == 422  # pydantic validation rejects unknown literal


def test_cancel_404_for_unknown(client):
    resp = client.post("/api/factory/unknown/cancel")
    assert resp.status_code == 404


def test_artifact_returns_409_when_not_released(client):
    if not ATOMS_DIR.exists():
        pytest.skip("atoms dir missing")
    start = client.post("/api/factory/start", json={"nl": "测试"})
    sid = start.json()["session_id"]
    resp = client.get(f"/api/factory/{sid}/artifact")
    assert resp.status_code == 409


@pytest.mark.skip(
    reason=(
        "Full HTTP lifecycle test depends on FastAPI BackgroundTasks running "
        "with patched LLMClient across thread boundaries. The unit-level API "
        "tests cover route correctness; E2E lifecycle is verified by "
        "test_session_e2e.py at the impl level (not over HTTP). Re-enable "
        "with real LLM in Phase 4 evaluation harness."
    )
)
def test_full_lifecycle_via_api(client):
    """Start -> wait for GATE_DESIGN -> pass through 6 gates -> verify RELEASED."""
    if not ATOMS_DIR.exists():
        pytest.skip("atoms dir missing")
    start = client.post("/api/factory/start", json={"nl": "完整生命周期测试"})
    sid = start.json()["session_id"]

    # Poll until session reaches a Gate (background task advances design stage)
    deadline = time.time() + 15
    state = None
    while time.time() < deadline:
        time.sleep(0.5)
        r = client.get(f"/api/factory/{sid}")
        state = r.json()["state"]
        if state.startswith("GATE_") or state in ("FAILED", "CANCELLED"):
            break
    assert state == "GATE_DESIGN", f"expected GATE_DESIGN, got {state}"

    # Pass through all 6 gates
    for _ in range(6):
        r = client.post(f"/api/factory/{sid}/gate", json={"decision": "pass"})
        assert r.status_code == 200, f"gate failed: {r.text}"
        # Wait for next stage to advance to next Gate (or RELEASED on last)
        deadline2 = time.time() + 10
        while time.time() < deadline2:
            time.sleep(0.5)
            r = client.get(f"/api/factory/{sid}")
            s = r.json()["state"]
            if s.startswith("GATE_") or s == "RELEASED":
                break

    final = client.get(f"/api/factory/{sid}")
    assert final.json()["state"] == "RELEASED"

    # Artifact should now be retrievable
    art = client.get(f"/api/factory/{sid}/artifact")
    assert art.status_code == 200
    assert "content" in art.json()
    assert "workflow:" in art.json()["content"]
