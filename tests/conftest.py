from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import get_settings


@pytest.fixture
def gateway_base_url() -> str:
    """Return the mocked gateway base URL."""
    return "https://iruidong.com/v1"


@pytest.fixture(autouse=True)
def env_settings(monkeypatch: pytest.MonkeyPatch, gateway_base_url: str) -> None:
    """Inject environment variables for tests."""
    monkeypatch.setenv("SANDBOX_API_BASE", gateway_base_url)
    monkeypatch.setenv("RUIDONG_API_KEY", "test-api-key")
    monkeypatch.setenv("HARNESS_ENV", "sandbox")
    monkeypatch.setenv("HARNESS_DEFAULT_MODEL", "gpt-4o-mini")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
