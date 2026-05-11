"""Phase 10 W4 D1 - tests for DifyKeyProvisioner."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.delivery.dify_key_provisioner import (
    DifyKeyProvisioner,
    KeyProvisioned,
)


def _fake_publisher(
    *,
    list_responses: dict[str, tuple[int, dict]] | None = None,
    create_responses: dict[str, tuple[int, dict]] | None = None,
    base_url: str = "http://localhost:8080",
):
    """Build a publisher-like mock the provisioner can drive."""
    pub = MagicMock()
    pub.base_url = base_url
    pub._wait_console_ready = MagicMock()
    pub._ensure_admin = MagicMock()
    pub._ensure_logged_in = MagicMock()
    pub._csrf_headers = MagicMock(return_value={"X-CSRF-Token": "t"})
    list_responses = list_responses or {}
    create_responses = create_responses or {}

    def _get_json(url: str, headers=None, timeout=None):
        for app_id, resp in list_responses.items():
            if f"/apps/{app_id}/api-keys" in url:
                return resp
        return 200, {"data": []}  # default: no existing keys

    def _post_json(url: str, payload, headers=None, timeout=None):
        for app_id, resp in create_responses.items():
            if f"/apps/{app_id}/api-keys" in url:
                return resp
        return 200, {"id": "auto-id", "token": f"tok-{url.rsplit('/', 2)[1]}"}

    pub._get_json = _get_json
    pub._post_json = _post_json
    return pub


@pytest.fixture
def log_dir(tmp_path: Path) -> Path:
    return tmp_path / "deploy_log"


def test_creates_key_when_none_exists(log_dir: Path):
    pub = _fake_publisher()
    p = DifyKeyProvisioner(pub, log_dir=log_dir)
    result = p.provision("sys", {"booking": "app-booking-uuid"})

    assert result.success_count == 1
    assert result.keys[0].token == "tok-app-booking-uuid"
    assert result.keys[0].reused is False


def test_reuses_existing_key(log_dir: Path):
    pub = _fake_publisher(list_responses={
        "app-booking-uuid": (200, {"data": [{"id": "k1", "token": "existing-token"}]}),
    })
    p = DifyKeyProvisioner(pub, log_dir=log_dir)
    result = p.provision("sys", {"booking": "app-booking-uuid"})

    assert result.keys[0].token == "existing-token"
    assert result.keys[0].reused is True


def test_writes_keys_json(log_dir: Path):
    pub = _fake_publisher()
    p = DifyKeyProvisioner(pub, log_dir=log_dir)
    result = p.provision("airline-cs", {"triage": "tid", "booking": "bid"})

    assert result.keys_log_path is not None
    assert result.keys_log_path.exists()
    data = json.loads(result.keys_log_path.read_text(encoding="utf-8"))
    assert data["system_slug"] == "airline-cs"
    assert len(data["keys"]) == 2


def test_writes_env_file_with_normalized_keys(log_dir: Path):
    pub = _fake_publisher()
    p = DifyKeyProvisioner(pub, log_dir=log_dir)
    result = p.provision("sys", {"booking-pro": "bid", "seat.v2": "sid"})

    content = result.env_file_path.read_text(encoding="utf-8")
    assert "DIFY_APP_API_KEY_BOOKING_PRO=" in content
    assert "DIFY_APP_API_KEY_SEAT_V2=" in content


def test_env_file_includes_warning_header(log_dir: Path):
    pub = _fake_publisher()
    p = DifyKeyProvisioner(pub, log_dir=log_dir)
    result = p.provision("sys", {"x": "xid"})
    content = result.env_file_path.read_text(encoding="utf-8")
    assert "Phase 10" in content
    assert ".gitignore" in content.lower() or "gitignore" in content.lower()


def test_missing_app_id_recorded_as_error(log_dir: Path):
    pub = _fake_publisher()
    p = DifyKeyProvisioner(pub, log_dir=log_dir)
    result = p.provision("sys", {"booking": ""})  # empty app_id

    assert result.fail_count == 1
    assert "missing dify_app_id" in result.keys[0].error


def test_create_failure_recorded_as_error(log_dir: Path):
    pub = _fake_publisher(create_responses={
        "bad-app": (500, {"error": "boom"}),
    })
    p = DifyKeyProvisioner(pub, log_dir=log_dir)
    result = p.provision("sys", {"booking": "bad-app"})

    assert result.fail_count == 1
    assert "HTTP 500" in result.keys[0].error


def test_create_with_no_token_in_body_errors(log_dir: Path):
    pub = _fake_publisher(create_responses={
        "weird-app": (200, {"id": "k1"}),  # no 'token' field
    })
    p = DifyKeyProvisioner(pub, log_dir=log_dir)
    result = p.provision("sys", {"booking": "weird-app"})

    assert result.fail_count == 1
    assert "no token" in result.keys[0].error


def test_failed_agent_shown_in_env_file_as_comment(log_dir: Path):
    pub = _fake_publisher(create_responses={
        "bad-app": (500, {"error": "boom"}),
    })
    p = DifyKeyProvisioner(pub, log_dir=log_dir)
    result = p.provision("sys", {"booking": "bad-app"})
    content = result.env_file_path.read_text(encoding="utf-8")
    # the failed agent line is a comment, not an exported var
    assert "# DIFY_APP_API_KEY_BOOKING=" in content


def test_multi_agent_full_provision(log_dir: Path):
    pub = _fake_publisher()
    p = DifyKeyProvisioner(pub, log_dir=log_dir)
    mapping = {"triage": "tid", "booking": "bid", "seat": "sid", "refund": "rid"}
    result = p.provision("airline", mapping)

    assert result.success_count == 4
    assert {k.agent_id for k in result.keys} == set(mapping)


def test_short_summary_format(log_dir: Path):
    pub = _fake_publisher()
    p = DifyKeyProvisioner(pub, log_dir=log_dir)
    result = p.provision("sys", {"x": "xid"})
    assert "system=sys" in result.short_summary()
    assert "1/1 ok" in result.short_summary()


def test_key_provisioned_ok_property():
    ok = KeyProvisioned(agent_id="x", dify_app_id="a", token="t")
    bad = KeyProvisioned(agent_id="y", dify_app_id="b", error="oops")
    no_token = KeyProvisioned(agent_id="z", dify_app_id="c")
    assert ok.ok is True
    assert bad.ok is False
    assert no_token.ok is False
