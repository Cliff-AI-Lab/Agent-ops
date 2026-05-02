"""harvest CLI - semi-automated prompt asset harvesting from GitHub sources.

Phase 2 W5 deliverable per [[可行性审计-v2]] § 缺口 2:
  Phase 1 manual / Phase 2 起 CLI 半自动化

V1 supports one source format: f/awesome-chatgpt-prompts CSV (act,prompt).
Each row -> capabilities/prompts/<sub>/<slug>.v1.yaml with provenance tracking.

Adding new source formats is mechanical: implement HarvestSource protocol,
register in SOURCES dict.

Exit codes:
    0 ok
    1 fetch failed (network / 4xx / 5xx)
    2 args error
    3 partial: some rows skipped (logged)
"""
from __future__ import annotations

import argparse
import csv
import io
import re
import sys
from pathlib import Path
from typing import Any, Iterable

import httpx
import yaml

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass


_SLUG_RE = re.compile(r"[^a-z0-9]+")
_NON_ASCII = re.compile(r"[^\x00-\x7f]+")


def _slug(s: str, max_len: int = 32) -> str:
    """Short ASCII slug for asset_id segment."""
    s = s.strip().lower()
    s = _NON_ASCII.sub("", s)
    s = _SLUG_RE.sub("_", s).strip("_")
    return s[:max_len] or "unnamed"


def _harvest_awesome_csv(
    csv_text: str,
    *,
    sub: str,
    license_id: str,
    source_url: str,
) -> list[dict[str, Any]]:
    """Parse f/awesome-chatgpt-prompts CSV into PromptDef-compatible dicts."""
    reader = csv.DictReader(io.StringIO(csv_text))
    out: list[dict[str, Any]] = []
    for row in reader:
        act = (row.get("act") or "").strip()
        body = (row.get("prompt") or "").strip()
        if not act or len(body) < 20:
            continue  # skip too-short bodies (PromptDef min_length=20)
        slug = _slug(act)
        out.append(
            {
                "schema_version": "1.0",
                "asset_id": f"prompt.{sub}.{slug}.v1",
                "type": "prompt",
                "version": "1.0.0",
                "name": act,
                "description": (
                    f"Harvested from f/awesome-chatgpt-prompts: {act}. "
                    f"Original 'act as' style role-play prompt template."
                ),
                "tags": ["harvested", "role_play", sub],
                "inputs": [
                    {
                        "name": "user_message",
                        "type": "string",
                        "required": True,
                        "description": "user follow-up turn",
                    }
                ],
                "output": {
                    "format": "text",
                    "x_data_type": "natural_language",
                },
                "template": body + "\n\nUser: {{ user_message }}",
                "test_cases": [
                    {
                        "name": "harvest_smoke",
                        "inputs": {"user_message": "你好"},
                        "expected_categories": [act.lower()],
                    }
                ],
                "linked_task_type": "通用对话",
                "linked_size": "中",
                "metrics": {"golden_set_pass_rate": None, "p95_latency_ms": None, "cost_per_call_cny": None},
                "maintainer": "harvest-bot / 2026-05",
                "provenance": {
                    "built_at": "2026-05-02T00:00:00Z",
                    "built_by": "github_harvest",
                    "source_url": source_url,
                    "source_path": "prompts.csv",
                    "license": license_id,
                    "license_safe": True,
                },
            }
        )
    return out


# Source registry — extend by adding HarvestSource entries.
SOURCES = {
    "awesome-chatgpt-prompts": {
        "raw_url": "https://raw.githubusercontent.com/f/awesome-chatgpt-prompts/main/prompts.csv",
        "license": "CC0-1.0",
        "default_sub": "role_play",
        "parser": _harvest_awesome_csv,
    },
}


def _fetch(url: str, timeout: float = 30.0) -> str:
    resp = httpx.get(url, timeout=timeout, follow_redirects=True)
    resp.raise_for_status()
    return resp.text


def _write_yaml(prompt: dict[str, Any], out_dir: Path, *, dry_run: bool = False) -> Path:
    sub = prompt["asset_id"].split(".")[1]
    slug = prompt["asset_id"].split(".")[2]
    target_dir = out_dir / sub
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{slug}.v1.yaml"
    if dry_run:
        return target
    text = yaml.safe_dump(prompt, allow_unicode=True, sort_keys=False)
    target.write_text(text, encoding="utf-8")
    return target


def harvest(
    source: str,
    *,
    out_dir: Path,
    fetcher=_fetch,  # injectable for tests
    limit: int | None = None,
    sub: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run harvest for one source. Returns summary dict."""
    if source not in SOURCES:
        raise KeyError(f"unknown source {source!r}; available: {list(SOURCES)}")
    spec = SOURCES[source]
    text = fetcher(spec["raw_url"])
    target_sub = sub or spec["default_sub"]

    parsed = spec["parser"](
        text,
        sub=target_sub,
        license_id=spec["license"],
        source_url=spec["raw_url"].rsplit("/", 1)[0],
    )
    if limit is not None:
        parsed = parsed[:limit]

    written: list[Path] = []
    skipped: list[str] = []
    seen: set[str] = set()
    for prompt in parsed:
        if prompt["asset_id"] in seen:
            skipped.append(f"duplicate: {prompt['asset_id']}")
            continue
        seen.add(prompt["asset_id"])
        path = _write_yaml(prompt, out_dir, dry_run=dry_run)
        written.append(path)
    return {
        "source": source,
        "fetched_count": len(parsed),
        "written_count": len(written),
        "skipped_count": len(skipped),
        "skipped_reasons": skipped,
        "out_dir": str(out_dir),
        "dry_run": dry_run,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="factory-harvest", description="Semi-auto prompt harvester")
    parser.add_argument("source", choices=list(SOURCES), help="source key")
    parser.add_argument("--out-dir", "-o", default="capabilities/prompts", help="prompt YAML root")
    parser.add_argument("--limit", type=int, default=None, help="max prompts to write")
    parser.add_argument("--sub", default=None, help="override asset_id subcategory segment")
    parser.add_argument("--dry-run", action="store_true", help="parse only, no file writes")
    args = parser.parse_args(argv)
    try:
        summary = harvest(
            args.source,
            out_dir=Path(args.out_dir),
            limit=args.limit,
            sub=args.sub,
            dry_run=args.dry_run,
        )
    except httpx.HTTPError as exc:
        print(f"[harvest] fetch failed: {exc}", file=sys.stderr)
        return 1
    except KeyError as exc:
        print(f"[harvest] {exc}", file=sys.stderr)
        return 2
    print(
        f"[harvest] source={summary['source']} fetched={summary['fetched_count']} "
        f"written={summary['written_count']} skipped={summary['skipped_count']} "
        f"out={summary['out_dir']}{' (dry-run)' if args.dry_run else ''}"
    )
    return 3 if summary["skipped_count"] > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
