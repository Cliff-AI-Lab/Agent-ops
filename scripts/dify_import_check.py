"""dify_import_check.py - Phase 8 Day 1 真机验证脚本

Run after `docker compose up -d` for the local Dify cloned at
`参考/dify/docker/`. This script:

1. Waits for the Dify console API to be reachable.
2. Creates an admin account on first run (POST /console/api/setup).
3. Logs in (POST /console/api/login) to get an access token.
4. Imports the compiler-emitted YAML fixture
   (`backend/app/core/pipelines/factory/compiler/tests/fixtures/compiled_daily_report_demo.yaml`)
   via POST /console/api/apps/imports.
5. Prints the import status (COMPLETED / PENDING / FAILED + diagnostics).

This is the import-check that V1 compiler V1 never had — its absence is
exactly what made `compiler/impl.py` "roughly matching" rather than
provably importable.

Usage::

    python scripts/dify_import_check.py
    python scripts/dify_import_check.py --base http://localhost:8080
    python scripts/dify_import_check.py --yaml /path/to/some.yaml

Environment overrides (no hardcoded secrets):
    DIFY_BASE_URL           default http://localhost:8080
    DIFY_ADMIN_EMAIL        default admin@agent-ops.local
    DIFY_ADMIN_NAME         default agent-ops-admin
    DIFY_ADMIN_PASSWORD     default ChangeMe!ops2026 (only used on first setup)
"""

from __future__ import annotations

import argparse
import base64
import http.cookiejar
import json
import os
import sys
import time
from pathlib import Path

import urllib.error
import urllib.request


# Dify 1.x stores access_token / refresh_token / csrf_token in HTTP cookies
# instead of returning them in the response body. We use a single opener with
# a cookiejar so that all subsequent requests (import, etc.) carry the session.
_cookies = http.cookiejar.CookieJar()
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_cookies))


def _post_json(url: str, payload: dict, headers: dict | None = None, timeout: int = 30) -> tuple[int, dict]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with _opener.open(req, timeout=timeout) as resp:
            data = resp.read().decode("utf-8")
            return resp.getcode(), json.loads(data) if data else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body)
        except Exception:
            parsed = {"raw": body}
        return e.code, parsed


def _get_json(url: str, headers: dict | None = None, timeout: int = 10) -> tuple[int, dict]:
    req = urllib.request.Request(url, method="GET")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with _opener.open(req, timeout=timeout) as resp:
            return resp.getcode(), json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        return e.code, {"raw": e.read().decode("utf-8", errors="replace")}
    except urllib.error.URLError:
        return 0, {}


def _cookie_value(name: str) -> str | None:
    for c in _cookies:
        if c.name == name:
            return c.value
    return None


def wait_console_ready(base: str, max_wait: int = 300) -> bool:
    print(f"[wait] polling {base}/console/api/setup ...")
    deadline = time.time() + max_wait
    while time.time() < deadline:
        code, _ = _get_json(f"{base}/console/api/setup")
        if code == 200:
            print(f"[wait] console reachable (HTTP {code})")
            return True
        time.sleep(5)
    print(f"[wait] timed out after {max_wait}s")
    return False


def ensure_admin(base: str, email: str, name: str, password: str) -> bool:
    code, body = _get_json(f"{base}/console/api/setup")
    step = body.get("step")
    print(f"[setup] current step={step}")
    if step == "finished":
        return True
    code, body = _post_json(
        f"{base}/console/api/setup",
        {"email": email, "name": name, "password": password},
    )
    print(f"[setup] POST /setup -> {code} {body}")
    return code in (200, 201)


def login(base: str, email: str, password: str) -> bool:
    # Dify console base64-encodes sensitive fields (libs/encryption.py
    # FieldEncryption.decrypt_field). Not cryptographic - just transport-layer
    # obfuscation. We must encode to match the @decrypt_password_field decorator.
    encoded_pw = base64.b64encode(password.encode("utf-8")).decode("ascii")
    code, body = _post_json(
        f"{base}/console/api/login",
        {"email": email, "password": encoded_pw, "remember_me": False},
    )
    print(f"[login] HTTP {code}, body={body}")
    if code != 200 or body.get("result") != "success":
        return False
    # Dify 1.x: tokens are in cookies (access_token / refresh_token / csrf_token).
    cookies = sorted([c.name for c in _cookies])
    print(f"[login] cookies set: {cookies}")
    return _cookie_value("access_token") is not None or _cookie_value("session") is not None or len(cookies) > 0


def import_yaml(base: str, yaml_text: str, app_name: str) -> tuple[int, dict]:
    payload = {
        "mode": "yaml-content",
        "yaml_content": yaml_text,
        "name": app_name,
    }
    headers: dict = {}
    csrf = _cookie_value("csrf_token")
    if csrf:
        # Dify CSRF protection: header name varies by version; try common ones.
        headers["X-CSRF-Token"] = csrf
        headers["X-CSRFToken"] = csrf
    code, body = _post_json(
        f"{base}/console/api/apps/imports",
        payload,
        headers=headers,
        timeout=60,
    )
    return code, body


def main():
    p = argparse.ArgumentParser(description="Dify real-machine import check")
    p.add_argument(
        "--base",
        default=os.getenv("DIFY_BASE_URL", "http://localhost:8080"),
        help="Dify base URL (default: $DIFY_BASE_URL or http://localhost:8080)",
    )
    p.add_argument(
        "--yaml",
        default=str(
            Path(__file__).resolve().parents[1]
            / "backend"
            / "app"
            / "core"
            / "pipelines"
            / "factory"
            / "compiler"
            / "tests"
            / "fixtures"
            / "compiled_daily_report_demo.yaml"
        ),
        help="Path to YAML to import",
    )
    p.add_argument("--name", default="agent-ops-daily-report-demo", help="App name in Dify")
    p.add_argument("--max-wait", type=int, default=300, help="Max seconds to wait for console")
    args = p.parse_args()

    base = args.base.rstrip("/")
    yaml_path = Path(args.yaml)
    if not yaml_path.exists():
        print(f"[fatal] YAML not found: {yaml_path}", file=sys.stderr)
        sys.exit(2)

    yaml_text = yaml_path.read_text(encoding="utf-8")
    print(f"[load] yaml from {yaml_path} ({len(yaml_text)} chars)")

    if not wait_console_ready(base, max_wait=args.max_wait):
        sys.exit(3)

    email = os.getenv("DIFY_ADMIN_EMAIL", "admin@agent-ops.local")
    name = os.getenv("DIFY_ADMIN_NAME", "agent-ops-admin")
    password = os.getenv("DIFY_ADMIN_PASSWORD", "ChangeMe!ops2026")

    if not ensure_admin(base, email, name, password):
        print("[fatal] admin setup failed", file=sys.stderr)
        sys.exit(4)

    if not login(base, email, password):
        print("[fatal] login failed", file=sys.stderr)
        sys.exit(5)

    code, body = import_yaml(base, yaml_text, args.name)
    print(f"[import] HTTP {code}")
    print(json.dumps(body, ensure_ascii=False, indent=2))

    if code == 200:
        status = body.get("status")
        if status in ("completed", "completed-with-warnings"):
            print("[result] PASS - YAML imported")
            sys.exit(0)
    elif code == 202:
        print("[result] PENDING - import needs confirmation; check_dependencies + confirm next")
        sys.exit(6)

    print("[result] FAIL")
    if isinstance(body, dict):
        for k in ("message", "error", "raw"):
            if k in body:
                print(f"  {k}: {body[k]}")
    sys.exit(1)


if __name__ == "__main__":
    main()
