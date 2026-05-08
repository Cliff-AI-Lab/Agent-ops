"""dify_import_check.py - thin wrapper around DifyPublisher.

Phase 8 Day 1 created this script as a one-off probe (curl-style logic
inline). Day 3 lifted the auth + cookie + import logic into
``backend/app/delivery/dify_publisher.py`` so that both this script AND
``harness factory deploy`` can share it. This file is now a thin shim
kept for backwards compatibility with Day 1 muscle memory.

Use ``harness factory deploy`` for new work. This script just probes a
single fixture.

Usage::

    python scripts/dify_import_check.py
    python scripts/dify_import_check.py --base http://localhost:8080
    python scripts/dify_import_check.py --yaml /path/to/some.yaml
    python scripts/dify_import_check.py --specimen-id ad-hoc-probe

Environment overrides (no hardcoded secrets):
    DIFY_BASE_URL           default http://localhost:8080
    DIFY_ADMIN_EMAIL        default admin@agent-ops.local
    DIFY_ADMIN_NAME         default agent-ops-admin
    DIFY_ADMIN_PASSWORD     default ChangeMe!ops2026 (only used on first setup)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

# allow running from agent-harness root: python scripts/dify_import_check.py
_HARNESS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_HARNESS_ROOT / "backend"))

from app.delivery.dify_publisher import DifyPublisher  # noqa: E402


def main():
    p = argparse.ArgumentParser(description="Dify real-machine import check")
    p.add_argument(
        "--base",
        default=os.getenv("DIFY_BASE_URL", "http://localhost:8080"),
    )
    p.add_argument(
        "--yaml",
        default=str(
            _HARNESS_ROOT
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
    )
    p.add_argument("--specimen-id", default="ad-hoc-probe")
    p.add_argument("--name", default=None)
    args = p.parse_args()

    yaml_path = Path(args.yaml)
    if not yaml_path.exists():
        print(f"[fatal] YAML not found: {yaml_path}", file=sys.stderr)
        sys.exit(2)
    yaml_text = yaml_path.read_text(encoding="utf-8")
    print(f"[load] yaml from {yaml_path} ({len(yaml_text)} chars)")

    publisher = DifyPublisher(base_url=args.base)
    try:
        res = publisher.publish(args.specimen_id, yaml_text, app_name=args.name)
    except RuntimeError as exc:
        print(f"[fatal] {exc}", file=sys.stderr)
        sys.exit(3)

    print(json.dumps(asdict(res), ensure_ascii=False, indent=2))
    if res.error:
        print(f"[result] FAIL: {res.error}", file=sys.stderr)
        sys.exit(1)
    print(
        f"[result] PASS - specimen={res.specimen_id} "
        f"app_id={res.dify_app_id} status={res.status}"
    )


if __name__ == "__main__":
    main()
