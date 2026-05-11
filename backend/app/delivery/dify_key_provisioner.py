"""DifyKeyProvisioner - Phase 10 W4 D1 Dify app API key provisioning.

W3 D3 injected handoff webhook nodes whose Authorization headers read
``${DIFY_APP_API_KEY_<TARGET>}`` env vars. This module fills in those
env vars by talking to Dify's console API:

  GET  /console/api/apps/<app_id>/api-keys   list existing keys
  POST /console/api/apps/<app_id>/api-keys   create a new key (returns
                                              {id, token, last_used_at})

For each app in the system's mapping JSON we:
  1. list keys; if any exists, reuse the first (idempotent)
  2. otherwise POST to create one
  3. write {agent_id -> token} to
     ``<harness>/.factory_deploy_log/<slug>.api-keys.json`` (gitignored)
  4. emit a ``.env.dify-apps`` file with DIFY_APP_API_KEY_<TARGET>=<token>
     lines that the runtime / docker-compose can source

R3 reuse audit:
  - Reuses DifyPublisher's auth machinery (cookies, CSRF, base64 password)
  - Reads mapping JSON written by MultiAgentDifyProjector (W3 D2)
  - No schema migration

Hard rule: tokens are NEVER inlined into committed YAML; only into the
operational state files under .factory_deploy_log/ (gitignored) and
.env.dify-apps (also gitignored, per project .gitignore).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.delivery.dify_publisher import DifyPublisher


logger = logging.getLogger(__name__)


@dataclass
class KeyProvisioned:
    """Per-agent key provisioning outcome."""

    agent_id: str
    dify_app_id: str
    token: str = ""
    reused: bool = False  # true if an existing key was reused
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.token) and not self.error


@dataclass
class KeyProvisionResult:
    """Aggregate outcome of provision()."""

    system_slug: str
    keys: list[KeyProvisioned] = field(default_factory=list)
    env_file_path: Path | None = None
    keys_log_path: Path | None = None

    @property
    def success_count(self) -> int:
        return sum(1 for k in self.keys if k.ok)

    @property
    def fail_count(self) -> int:
        return sum(1 for k in self.keys if not k.ok)

    def short_summary(self) -> str:
        return (
            f"system={self.system_slug} "
            f"keys={self.success_count}/{len(self.keys)} ok"
            + (f" ({self.fail_count} failed)" if self.fail_count else "")
        )


class DifyKeyProvisioner:
    """Provisions Dify Service API keys for a multi-agent system.

    Args:
        publisher: DifyPublisher (provides auth + cookie session +
                   internal _get_json / _post_json helpers).
        log_dir: where to write the per-system api-keys.json + .env file
                 (default: <harness>/.factory_deploy_log).
    """

    def __init__(
        self,
        publisher: "DifyPublisher",
        log_dir: Path | str | None = None,
    ):
        self.publisher = publisher
        self.log_dir = Path(log_dir) if log_dir else self._default_log_dir()
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def provision(
        self,
        system_slug: str,
        agent_to_app_id: dict[str, str],
    ) -> KeyProvisionResult:
        """Ensure each agent has a Dify Service API key.

        Args:
            system_slug: used to name the output files
            agent_to_app_id: {"triage": "uuid", "booking": "uuid", ...}

        Returns:
            KeyProvisionResult with per-agent outcomes + the env file path
            that the W3-injected webhook nodes can source.
        """
        result = KeyProvisionResult(system_slug=system_slug)
        # Login is idempotent inside publisher; calling on first request is safe.
        self.publisher._wait_console_ready()  # noqa: SLF001
        self.publisher._ensure_admin()  # noqa: SLF001
        self.publisher._ensure_logged_in()  # noqa: SLF001

        for agent_id, app_id in agent_to_app_id.items():
            if not app_id:
                result.keys.append(KeyProvisioned(
                    agent_id=agent_id, dify_app_id="",
                    error="missing dify_app_id (agent not published yet)",
                ))
                continue
            outcome = self._ensure_key(agent_id, app_id)
            result.keys.append(outcome)

        result.keys_log_path = self._write_keys_json(system_slug, result.keys)
        result.env_file_path = self._write_env_file(system_slug, result.keys)
        return result

    # ----- internals ----------------------------------------------------

    def _ensure_key(self, agent_id: str, app_id: str) -> KeyProvisioned:
        list_url = f"{self.publisher.base_url}/console/api/apps/{app_id}/api-keys"
        code, body = self.publisher._get_json(  # noqa: SLF001
            list_url, headers=self.publisher._csrf_headers(),  # noqa: SLF001
        )
        if code == 200 and isinstance(body, dict):
            existing = body.get("data") or []
            if existing and isinstance(existing[0], dict):
                token = existing[0].get("token", "")
                if token:
                    return KeyProvisioned(
                        agent_id=agent_id, dify_app_id=app_id,
                        token=token, reused=True,
                    )

        code, body = self.publisher._post_json(  # noqa: SLF001
            list_url, payload={},
            headers=self.publisher._csrf_headers(),  # noqa: SLF001
        )
        if code not in (200, 201):
            return KeyProvisioned(
                agent_id=agent_id, dify_app_id=app_id,
                error=f"key create HTTP {code}: {body}",
            )
        if not isinstance(body, dict):
            return KeyProvisioned(
                agent_id=agent_id, dify_app_id=app_id,
                error=f"unexpected key create response shape: {body}",
            )
        token = body.get("token") or body.get("data", {}).get("token", "")
        if not token:
            return KeyProvisioned(
                agent_id=agent_id, dify_app_id=app_id,
                error=f"key create returned no token: {body}",
            )
        return KeyProvisioned(
            agent_id=agent_id, dify_app_id=app_id,
            token=token, reused=False,
        )

    def _write_keys_json(self, system_slug: str, keys: list[KeyProvisioned]) -> Path:
        path = self.log_dir / f"{self._slug(system_slug)}.api-keys.json"
        payload = {
            "system_slug": system_slug,
            "keys": [asdict(k) for k in keys],
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        return path

    def _write_env_file(self, system_slug: str, keys: list[KeyProvisioned]) -> Path:
        path = self.log_dir / f"{self._slug(system_slug)}.env.dify-apps"
        lines: list[str] = [
            "# Phase 10 W4 D1: Dify Service API keys for handoff webhooks.",
            "# Source this before launching the runtime / docker-compose:",
            "#   set -a && . path/to/this/file && set +a",
            "# Tokens are NEVER committed; this file is in .gitignore.",
            "",
        ]
        for k in keys:
            env_key = "DIFY_APP_API_KEY_" + self._normalize_env(k.agent_id)
            if k.ok:
                lines.append(f"{env_key}={k.token}")
            else:
                lines.append(f"# {env_key}=<missing: {k.error}>")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    @staticmethod
    def _normalize_env(name: str) -> str:
        return re.sub(r"[^A-Z0-9_]", "_", name.upper()) or "UNKNOWN"

    @staticmethod
    def _slug(name: str) -> str:
        out = []
        for ch in name:
            if ch.isalnum() or ch in "-_.":
                out.append(ch)
            else:
                out.append("_")
        return "".join(out).strip("._-") or "unnamed_system"

    @staticmethod
    def _default_log_dir() -> Path:
        return Path(__file__).resolve().parents[3] / ".factory_deploy_log"
