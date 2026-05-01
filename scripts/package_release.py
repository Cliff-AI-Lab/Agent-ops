"""Package the agent-ops project as a release zip with a versioned filename.

Usage:
    uv run python scripts/package_release.py

Reads version from pyproject.toml ([project].version) and writes the zip to
``dist/agent-ops-v{version}-{YYYYMMDD}.zip``.

Excludes:
  - .venv/, .pytest_cache/, .git/, __pycache__/
  - harness.db (per-machine state)
  - assets/ (generated artifacts; reproducible from the code)
  - agents/__generated__/ (same)
  - dist/ itself (avoid recursive packaging)
  - any *.zip at root
  - .env.dev / .env.prod / .env.sandbox (may contain secrets); .env.example IS included
"""
from __future__ import annotations

import re
import sys
import time
import zipfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
EXCLUDE_DIRS = {
    ".venv",
    ".pytest_cache",
    ".git",
    "__pycache__",
    "node_modules",
    "dist",
    "assets",
}
EXCLUDE_NAMED = {
    "harness.db",
    ".env.dev",
    ".env.prod",
    ".env.sandbox",
    "team-task-board.zip",
}
EXCLUDE_SUFFIX = {".pyc", ".pyo"}


def read_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, flags=re.MULTILINE)
    if not m:
        print("✗ couldn't find version in pyproject.toml", file=sys.stderr)
        sys.exit(2)
    return m.group(1)


def should_skip(path: Path) -> bool:
    if path.name in EXCLUDE_NAMED:
        return True
    if path.suffix in EXCLUDE_SUFFIX:
        return True
    parts = set(path.parts)
    if parts & EXCLUDE_DIRS:
        return True
    # agents/__generated__/* are runtime — exclude
    if "__generated__" in path.parts:
        return True
    # setuptools build artifacts
    if any(p.endswith(".egg-info") for p in path.parts):
        return True
    return False


def main() -> int:
    version = read_version()
    stamp = time.strftime("%Y%m%d")
    DIST.mkdir(parents=True, exist_ok=True)
    out = DIST / f"agent-ops-v{version}-{stamp}.zip"
    if out.exists():
        out.unlink()

    n_files = 0
    n_bytes = 0
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in ROOT.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(ROOT)
            if should_skip(rel):
                continue
            zf.write(path, arcname=str(Path("agent-harness") / rel))
            n_files += 1
            n_bytes += path.stat().st_size

    size_mb = out.stat().st_size / (1024 * 1024)
    print(f"✅ packaged {n_files} files ({n_bytes/1024/1024:.2f} MiB raw)")
    print(f"   → {out}")
    print(f"   → final zip size: {size_mb:.2f} MiB")
    print(f"   version: v{version} · stamp: {stamp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
