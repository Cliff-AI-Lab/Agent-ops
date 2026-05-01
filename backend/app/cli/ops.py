"""`ops` CLI — headless control of a running Agent Ops server (Batch H).

Talks to a running backend (default http://127.0.0.1:8000, override with
``OPS_BASE`` env). Designed for shell scripting / smoke tests, not as a
replacement for the workbench UI.

Subcommands:

    ops version
    ops health
    ops triage "用户消息"
    ops wiki search "周报"
    ops asset list [--status draft|active|archived]
    ops asset promote <asset_id>

Exit codes:
    0 — success (prints to stdout)
    1 — server unreachable / 5xx
    2 — bad arguments
    3 — server returned 4xx (auth / not found)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import httpx

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass


def _base_url() -> str:
    return os.environ.get("OPS_BASE", "http://127.0.0.1:8000").rstrip("/")


def _print_json(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _request(method: str, path: str, *, json_body: dict | None = None,
             timeout: float = 30.0) -> tuple[int, Any]:
    """Returns (exit_code, parsed_body). Treats network errors as exit_code=1."""
    url = f"{_base_url()}{path}"
    try:
        r = httpx.request(method, url, json=json_body, timeout=timeout)
    except httpx.HTTPError as exc:
        print(f"✗ network error contacting {url}: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 1, None
    if 500 <= r.status_code < 600:
        print(f"✗ server error {r.status_code}: {r.text[:240]}", file=sys.stderr)
        return 1, None
    if r.status_code >= 400:
        print(f"✗ {r.status_code}: {r.text[:240]}", file=sys.stderr)
        return 3, None
    try:
        return 0, r.json()
    except ValueError:
        # 200 with non-JSON body
        return 0, r.text


# -------------------- handlers --------------------
def cmd_version(_: argparse.Namespace) -> int:
    """Print the version reported by /openapi.json."""
    rc, body = _request("GET", "/openapi.json")
    if rc != 0:
        return rc
    info = (body or {}).get("info", {})
    print(f"Agent Ops · {info.get('title', '?')} · v{info.get('version', '?')}")
    return 0


def cmd_health(_: argparse.Namespace) -> int:
    rc, body = _request("GET", "/health")
    if rc != 0:
        return rc
    _print_json(body)
    return 0


def cmd_triage(args: argparse.Namespace) -> int:
    payload: dict[str, Any] = {"message": args.message}
    if args.session:
        payload["session_id"] = args.session
    if args.model:
        payload["model"] = args.model
    rc, body = _request("POST", "/api/triage", json_body=payload, timeout=120)
    if rc != 0:
        return rc
    if args.json:
        _print_json(body)
        return 0
    decision = (body or {}).get("decision", {})
    print(f"target          : {decision.get('target')}")
    print(f"target_id       : {decision.get('target_id') or '-'}")
    print(f"reason          : {decision.get('reason')}")
    print(f"forwarded_message: {decision.get('forwarded_message')}")
    print(f"fallback_used   : {body.get('fallback_used')}")
    return 0


def cmd_wiki_search(args: argparse.Namespace) -> int:
    qs = httpx.QueryParams({"q": args.query})
    rc, body = _request("GET", f"/api/wiki/search?{qs}")
    if rc != 0:
        return rc
    if args.json:
        _print_json(body)
        return 0
    body = body or {}
    # /api/wiki/search currently returns {tools:[], capabilities:[], total:N};
    # /api/wiki/all returns {entries:[{kind, id, ...}], total:N}; tolerate both.
    items: list[dict[str, Any]] = []
    if isinstance(body.get("entries"), list):
        items = list(body["entries"])
    elif isinstance(body.get("results"), list):
        items = list(body["results"])
    else:
        for tool in body.get("tools", []) or []:
            items.append({"kind": "atom", "id": tool.get("tool_id"), "name": tool.get("name", "")})
        for cap in body.get("capabilities", []) or []:
            items.append({"kind": "composite", "id": cap.get("capability_id"), "name": cap.get("name", "")})
    if not items:
        print("(no matches)")
        return 0
    for item in items:
        kind = item.get("kind", "?")
        eid = item.get("id", "?")
        name = item.get("name", "")
        print(f"  [{kind:>9}] {eid:<48} {name}")
    return 0


def cmd_asset_list(args: argparse.Namespace) -> int:
    qs = ""
    if args.status:
        qs = f"?status={args.status}"
    rc, body = _request("GET", f"/api/assets{qs}")
    if rc != 0:
        return rc
    if args.json:
        _print_json(body)
        return 0
    rows = (body or {}).get("assets") if isinstance(body, dict) else body
    if not rows:
        print("(no assets)")
        return 0
    print(f"  {'asset_id':<40} {'status':<10} {'product_type':<12} {'name'}")
    for a in rows:
        print(f"  {a.get('asset_id',''):<40} {a.get('status',''):<10} {a.get('product_type',''):<12} {a.get('name','')}")
    return 0


def cmd_asset_promote(args: argparse.Namespace) -> int:
    rc, body = _request(
        "POST",
        f"/api/assets/{args.asset_id}/promote",
        json_body={"promoted_by": args.by or "ops-cli"},
    )
    if rc != 0:
        return rc
    if args.json:
        _print_json(body)
        return 0
    asset = (body or {}).get("asset") or body or {}
    print(f"✓ promoted {asset.get('asset_id', args.asset_id)} → status={asset.get('status', '?')}")
    print(f"  active_in_registry: {body.get('active_in_registry') if isinstance(body, dict) else '?'}")
    return 0


# -------------------- argparse glue --------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ops",
        description="Headless CLI for a running Agent Ops backend.",
    )
    p.add_argument("--base", help="Override base URL (default: $OPS_BASE or http://127.0.0.1:8000)")
    p.add_argument("--json", action="store_true", help="Print raw JSON instead of pretty output")

    sp = p.add_subparsers(dest="cmd", required=True)

    sp.add_parser("version", help="Print server version (from /openapi.json)").set_defaults(handler=cmd_version)
    sp.add_parser("health", help="GET /health").set_defaults(handler=cmd_health)

    triage = sp.add_parser("triage", help="POST /api/triage — show routing decision")
    triage.add_argument("message", help="User message to route")
    triage.add_argument("--session", help="Optional session_id for richer context")
    triage.add_argument("--model", help="Override the triage light model")
    triage.set_defaults(handler=cmd_triage)

    wiki = sp.add_parser("wiki", help="Wiki / RegistryHub queries")
    wiki_sub = wiki.add_subparsers(dest="wiki_cmd", required=True)
    wiki_search = wiki_sub.add_parser("search", help="Free-text search across all tiers")
    wiki_search.add_argument("query")
    wiki_search.set_defaults(handler=cmd_wiki_search)

    asset = sp.add_parser("asset", help="Marketplace / Asset Hub")
    asset_sub = asset.add_subparsers(dest="asset_cmd", required=True)
    asset_list = asset_sub.add_parser("list", help="List generated assets")
    asset_list.add_argument("--status", choices=["draft", "active", "archived"])
    asset_list.set_defaults(handler=cmd_asset_list)
    asset_promote = asset_sub.add_parser("promote", help="Flip a draft asset to active")
    asset_promote.add_argument("asset_id")
    asset_promote.add_argument("--by", help="Set the promoted_by audit field (default: ops-cli)")
    asset_promote.set_defaults(handler=cmd_asset_promote)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "base", None):
        os.environ["OPS_BASE"] = args.base
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
