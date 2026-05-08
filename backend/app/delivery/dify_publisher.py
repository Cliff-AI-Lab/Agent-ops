"""Dify deployment service - factory's "one-button publish" engine.

This is the operational core of the lights-out factory's Dify lane:

    factory build  -> YAML  -> DifyPublisher.publish(specimen_id, yaml)
                                   |
                                   +-- creates app on first publish
                                   +-- reuses same dify_app_id on republish
                                   +-- writes deploy_log/<specimen_id>.json
                                   |
    factory drift  -> DifyPublisher.drift_check(specimen_id)
                                   |
                                   +-- pulls Dify export
                                   +-- compares sha256 vs deploy_log
                                   +-- reports drift but does NOT auto-pull
                                       (per Phase 8 Day 3 decision: one-way push)

The deploy_log is a per-specimen JSON file under
``<harness_root>/.factory_deploy_log/<specimen_id>.json``. It is NOT committed
(local operational state); fixtures for tests are committed separately.

Auth specifics for Dify 1.x discovered during Day 1 import-check:
  - login password must be base64-encoded (FieldEncryption obfuscation)
  - tokens come back as cookies (access_token / refresh_token / csrf_token),
    not in response body
  - subsequent requests must carry the cookie session
"""

from __future__ import annotations

import base64
import hashlib
import http.cookiejar
import json
import logging
import os
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class DeployLogEntry:
    """Per-specimen deployment record. Persisted as JSON."""

    specimen_id: str
    dify_app_id: str
    yaml_sha256: str
    dify_dsl_version: str
    deployed_at: str
    deploy_count: int
    factory_version: str
    last_import_status: str
    base_url: str

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)


@dataclass
class PublishResult:
    """Outcome of a publish call."""

    specimen_id: str
    dify_app_id: str
    status: str  # completed | completed-with-warnings | pending | failed
    is_first_deploy: bool
    deploy_count: int
    yaml_sha256: str
    error: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class DriftReport:
    """Outcome of a drift_check call."""

    specimen_id: str
    dify_app_id: str
    has_drift: bool
    factory_sha256: str
    dify_sha256: str | None
    reason: str  # ok | drift | dify_app_missing | export_failed
    detail: str = ""


def sha256_yaml(yaml_text: str) -> str:
    return hashlib.sha256(yaml_text.encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DifyPublisher:
    """Owns the factory <-> Dify connection.

    Construct once per CLI invocation; reuses cookie-jar across calls.
    Connection params via constructor or env vars
    (DIFY_BASE_URL / DIFY_ADMIN_EMAIL / DIFY_ADMIN_NAME / DIFY_ADMIN_PASSWORD).
    """

    def __init__(
        self,
        base_url: str | None = None,
        email: str | None = None,
        admin_name: str | None = None,
        password: str | None = None,
        log_dir: Path | str | None = None,
        request_timeout: int = 60,
    ):
        self.base_url = (base_url or os.getenv("DIFY_BASE_URL", "http://localhost:8080")).rstrip("/")
        self.email = email or os.getenv("DIFY_ADMIN_EMAIL", "admin@agent-ops.local")
        self.admin_name = admin_name or os.getenv("DIFY_ADMIN_NAME", "agent-ops-admin")
        self.password = password or os.getenv("DIFY_ADMIN_PASSWORD", "ChangeMe!ops2026")
        self.log_dir = Path(log_dir) if log_dir else self._default_log_dir()
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._timeout = request_timeout
        self._cookies = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._cookies)
        )
        self._logged_in = False

    # ----- public API -----------------------------------------------------

    def publish(
        self,
        specimen_id: str,
        yaml_text: str,
        *,
        app_name: str | None = None,
    ) -> PublishResult:
        """Push a compiled YAML to Dify, reusing app_id on republish."""
        sha = sha256_yaml(yaml_text)
        prior = self.read_deploy_log(specimen_id)
        is_first = prior is None

        self._wait_console_ready()
        self._ensure_admin()
        self._ensure_logged_in()

        existing_app_id = prior.dify_app_id if prior else None
        payload: dict[str, Any] = {
            "mode": "yaml-content",
            "yaml_content": yaml_text,
            "name": app_name or f"specimen_{specimen_id[:30]}",
        }
        if existing_app_id:
            payload["app_id"] = existing_app_id

        code, body = self._post_json(
            f"{self.base_url}/console/api/apps/imports",
            payload,
            headers=self._csrf_headers(),
            timeout=self._timeout,
        )

        ok = code == 200 and isinstance(body, dict) and body.get("status", "").startswith("completed")
        if not ok:
            return PublishResult(
                specimen_id=specimen_id,
                dify_app_id=existing_app_id or "",
                status=str(body.get("status") or f"http_{code}"),
                is_first_deploy=is_first,
                deploy_count=(prior.deploy_count if prior else 0),
                yaml_sha256=sha,
                error=str(body.get("error") or body.get("message") or body),
                raw=body if isinstance(body, dict) else {"raw": body},
            )

        new_app_id = body.get("app_id") or existing_app_id or ""
        deploy_count = (prior.deploy_count + 1) if prior else 1
        entry = DeployLogEntry(
            specimen_id=specimen_id,
            dify_app_id=new_app_id,
            yaml_sha256=sha,
            dify_dsl_version=body.get("imported_dsl_version") or "",
            deployed_at=_utc_now(),
            deploy_count=deploy_count,
            factory_version=os.getenv("FACTORY_VERSION", "v2.6.0-day3"),
            last_import_status=body.get("status", "unknown"),
            base_url=self.base_url,
        )
        self.write_deploy_log(entry)
        return PublishResult(
            specimen_id=specimen_id,
            dify_app_id=new_app_id,
            status=body.get("status", "unknown"),
            is_first_deploy=is_first,
            deploy_count=deploy_count,
            yaml_sha256=sha,
            raw=body,
        )

    def drift_check(self, specimen_id: str) -> DriftReport:
        """Compare deploy_log sha256 against current Dify export.

        Per Phase 8 Day 3 decision: REPORT drift, do not auto-pull.
        """
        entry = self.read_deploy_log(specimen_id)
        if entry is None:
            return DriftReport(
                specimen_id=specimen_id,
                dify_app_id="",
                has_drift=False,
                factory_sha256="",
                dify_sha256=None,
                reason="dify_app_missing",
                detail="no deploy_log entry; never deployed by factory",
            )

        self._wait_console_ready()
        self._ensure_admin()
        self._ensure_logged_in()

        code, body = self._get_json(
            f"{self.base_url}/console/api/apps/{entry.dify_app_id}/export",
            headers=self._csrf_headers(),
        )
        if code != 200 or "data" not in body:
            return DriftReport(
                specimen_id=specimen_id,
                dify_app_id=entry.dify_app_id,
                has_drift=False,
                factory_sha256=entry.yaml_sha256,
                dify_sha256=None,
                reason="export_failed",
                detail=f"HTTP {code} {body}",
            )

        dify_yaml = body["data"]
        dify_sha = sha256_yaml(dify_yaml)
        # Drift = current dify yaml sha differs from last factory push sha.
        # Note: Dify rewrites timestamps / ids on re-export so we WILL see drift
        # even if a human did not edit. Real drift detection in Phase 9 will
        # canonicalize before hashing; for Day 3 we surface raw mismatch + a
        # note that humans can ignore cosmetic drift.
        has_drift = dify_sha != entry.yaml_sha256
        return DriftReport(
            specimen_id=specimen_id,
            dify_app_id=entry.dify_app_id,
            has_drift=has_drift,
            factory_sha256=entry.yaml_sha256,
            dify_sha256=dify_sha,
            reason="drift" if has_drift else "ok",
            detail=(
                "raw sha mismatch (Dify re-serializes on export; "
                "Phase 9 will canonicalize)"
                if has_drift
                else ""
            ),
        )

    # ----- log persistence -----------------------------------------------

    def log_path(self, specimen_id: str) -> Path:
        safe = specimen_id.replace("/", "_").replace("\\", "_")
        return self.log_dir / f"{safe}.json"

    def read_deploy_log(self, specimen_id: str) -> DeployLogEntry | None:
        p = self.log_path(specimen_id)
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return DeployLogEntry(**data)
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning("deploy_log %s unreadable: %s", p, exc)
            return None

    def write_deploy_log(self, entry: DeployLogEntry) -> Path:
        p = self.log_path(entry.specimen_id)
        p.write_text(entry.to_json(), encoding="utf-8")
        return p

    # ----- internals ------------------------------------------------------

    @staticmethod
    def _default_log_dir() -> Path:
        # <harness root>/.factory_deploy_log
        # backend/app/delivery/dify_publisher.py -> harness root = parents[3]
        return Path(__file__).resolve().parents[3] / ".factory_deploy_log"

    def _wait_console_ready(self, max_wait: int = 60) -> None:
        deadline = time.time() + max_wait
        while time.time() < deadline:
            code, _ = self._get_json(f"{self.base_url}/console/api/setup", timeout=5)
            if code == 200:
                return
            time.sleep(3)
        raise RuntimeError(f"Dify console not reachable at {self.base_url}")

    def _ensure_admin(self) -> None:
        code, body = self._get_json(f"{self.base_url}/console/api/setup")
        if code == 200 and body.get("step") == "finished":
            return
        code, body = self._post_json(
            f"{self.base_url}/console/api/setup",
            {"email": self.email, "name": self.admin_name, "password": self.password},
        )
        if code not in (200, 201):
            raise RuntimeError(f"admin setup failed: HTTP {code} {body}")

    def _ensure_logged_in(self) -> None:
        if self._logged_in:
            return
        encoded_pw = base64.b64encode(self.password.encode("utf-8")).decode("ascii")
        code, body = self._post_json(
            f"{self.base_url}/console/api/login",
            {"email": self.email, "password": encoded_pw, "remember_me": False},
        )
        if code != 200 or body.get("result") != "success":
            raise RuntimeError(f"login failed: HTTP {code} {body}")
        if not any(c.name == "access_token" for c in self._cookies):
            raise RuntimeError("login returned 200 but no access_token cookie")
        self._logged_in = True

    def _csrf_headers(self) -> dict[str, str]:
        for c in self._cookies:
            if c.name == "csrf_token":
                return {"X-CSRF-Token": c.value, "X-CSRFToken": c.value}
        return {}

    def _post_json(
        self,
        url: str,
        payload: dict,
        headers: dict | None = None,
        timeout: int | None = None,
    ) -> tuple[int, dict]:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with self._opener.open(req, timeout=timeout or self._timeout) as resp:
                data = resp.read().decode("utf-8")
                return resp.getcode(), json.loads(data) if data else {}
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            try:
                return e.code, json.loads(raw)
            except Exception:
                return e.code, {"raw": raw}
        except urllib.error.URLError as e:
            return 0, {"raw": f"URLError: {e}"}

    def _get_json(
        self,
        url: str,
        headers: dict | None = None,
        timeout: int | None = None,
    ) -> tuple[int, dict]:
        req = urllib.request.Request(url, method="GET")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with self._opener.open(req, timeout=timeout or self._timeout) as resp:
                txt = resp.read().decode("utf-8")
                return resp.getcode(), json.loads(txt) if txt else {}
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            try:
                return e.code, json.loads(raw)
            except Exception:
                return e.code, {"raw": raw}
        except urllib.error.URLError:
            return 0, {}
