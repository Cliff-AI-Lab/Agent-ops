"""MarkdownGen — generate a standardized Markdown doc for any RegistryEntry (Batch Y).

Why: every registered ontology object should ship with a human-readable +
LLM-readable Markdown card so the IntentRouter, the Wiki UI, and downstream
agents share the **same** description format. Curated ``README.md`` next to
the source YAML is preferred; this generator is the deterministic fallback.

Header schema (every doc):
- # {Name} (`{kind}` · v{version})
- ## 用途
- ## 输入 / 输出
- ## 由什么组成 (composite/agent only)
- ## 调用方式
- ## 示例
- ## 限制 / 风险
"""
from __future__ import annotations

import json
from pathlib import Path

from app.registry.agent_store import get_agent_store
from app.registry.preset_store import get_preset_store
from app.registry.registry_hub import EntryKind, RegistryEntry, get_entry
from app.registry.store import get_store
from app.registry.tool_store import get_tool_store

CURATED_README_FILENAMES = ("README.md", "README.zh.md")


def render_markdown(entry: RegistryEntry) -> str:
    """Render a RegistryEntry into the canonical MD layout."""
    raw = entry.raw or {}
    parts: list[str] = []

    parts.append(f"# {entry.name}  (`{entry.kind}` · v{entry.version})")
    parts.append("")
    parts.append(f"> id: `{entry.id}` · status: **{entry.activation_status}**" + (" · is_generated" if entry.is_generated else ""))
    parts.append("")

    parts.append("## 用途")
    parts.append("")
    parts.append(entry.description.strip() or "_(no description)_")
    parts.append("")

    if entry.intent_keywords:
        parts.append("## 意图关键词 (IntentRouter 检索)")
        parts.append("")
        parts.append(", ".join(f"`{k}`" for k in entry.intent_keywords))
        parts.append("")

    if entry.tags:
        parts.append("## 标签")
        parts.append("")
        parts.append(", ".join(f"`{t}`" for t in entry.tags))
        parts.append("")

    # Inputs / Outputs (if the entry has schemas)
    input_schema = raw.get("input_schema")
    output_schema = raw.get("output_schema")
    if input_schema or output_schema:
        parts.append("## 输入 / 输出")
        parts.append("")
        if input_schema:
            parts.append("**input_schema**")
            parts.append("```json")
            parts.append(json.dumps(input_schema, ensure_ascii=False, indent=2))
            parts.append("```")
            parts.append("")
        if output_schema:
            parts.append("**output_schema**")
            parts.append("```json")
            parts.append(json.dumps(output_schema, ensure_ascii=False, indent=2))
            parts.append("```")
            parts.append("")

    # Composition (composite/agent layers)
    if entry.kind in {"composite", "agent"}:
        composes = raw.get("composes") or raw.get("capabilities_used") or []
        tools_used = raw.get("tools_used") or []
        if composes or tools_used:
            parts.append("## 由什么组成(三层金字塔)")
            parts.append("")
            if composes:
                parts.append("**capabilities_used / composes:**")
                for cid in composes:
                    parts.append(f"- `{cid}`")
                parts.append("")
            if tools_used:
                parts.append("**tools_used (atom):**")
                for tid in tools_used:
                    parts.append(f"- `{tid}`")
                parts.append("")

    # Transport (atom/agent)
    transport_type = raw.get("transport_type")
    endpoint = raw.get("endpoint")
    if transport_type:
        parts.append("## 调用方式")
        parts.append("")
        parts.append(f"- transport: `{transport_type}`")
        if endpoint:
            parts.append(f"- endpoint: `{endpoint}`")
        auth_mode = raw.get("auth_mode")
        if auth_mode:
            parts.append(f"- auth: `{auth_mode}`")
        auth_env = raw.get("auth_env_vars")
        if auth_env:
            parts.append(f"- auth env vars: {', '.join(f'`{e}`' for e in auth_env)}")
        parts.append("")

    # Examples (atoms have ToolExample, agents have AgentExample)
    examples = raw.get("examples") or []
    if examples:
        parts.append("## 示例")
        parts.append("")
        for ex in examples:
            title = ex.get("title", "示例")
            parts.append(f"**{title}**")
            user_intent = ex.get("user_intent")
            if user_intent:
                parts.append(f"- 用户意图: {user_intent}")
            inp = ex.get("input")
            if inp:
                parts.append("- input:")
                parts.append("  ```json")
                parts.append("  " + json.dumps(inp, ensure_ascii=False).replace("\n", "\n  "))
                parts.append("  ```")
            outcome = ex.get("expected_outcome") or ex.get("output_hint")
            if outcome:
                parts.append(f"- 预期输出: {outcome}")
            parts.append("")

    # Risks / Limits
    risk = raw.get("risk_level")
    forbidden = raw.get("forbidden_actions") or []
    cost_budget = raw.get("cost_budget")
    latency_budget = raw.get("latency_budget_ms")
    if risk or forbidden or cost_budget is not None or latency_budget:
        parts.append("## 限制 / 风险")
        parts.append("")
        if risk:
            parts.append(f"- risk_level: **{risk}**")
        if forbidden:
            parts.append(f"- 禁止动作: {', '.join(f'`{f}`' for f in forbidden)}")
        if cost_budget is not None:
            parts.append(f"- 单次预算: ${cost_budget}")
        if latency_budget:
            parts.append(f"- 延迟预算: {latency_budget}ms")
        parts.append("")

    # Generation provenance (asset-hub members)
    gen = raw.get("generation")
    if entry.is_generated and isinstance(gen, dict):
        parts.append("## 资产中心来源")
        parts.append("")
        for k in ("generation_id", "source_session_id", "source_sop", "generated_at",
                  "quality_score", "usage_count", "promoted_by"):
            v = gen.get(k)
            if v is not None and v != "":
                parts.append(f"- {k}: `{v}`")
        parts.append("")

    return "\n".join(parts).rstrip() + "\n"


def _curated_md_path(kind: EntryKind, entry_id: str) -> Path | None:
    """Look for a hand-written README.md next to the entry's source dir."""
    if kind == "agent":
        src = get_agent_store().source_dir(entry_id)
    elif kind == "preset":
        src = get_preset_store().source_dir(entry_id)
    elif kind == "atom":
        # Tools are file-per-yaml; we don't track per-tool dirs. Skip curated.
        return None
    elif kind == "composite":
        # Capabilities are file-per-yaml; same as tools.
        return None
    else:
        return None
    if src is None:
        return None
    for name in CURATED_README_FILENAMES:
        candidate = src / name
        if candidate.is_file():
            return candidate
    return None


def markdown_for(kind: EntryKind, entry_id: str) -> tuple[str, str]:
    """Return ``(markdown, source)`` where source is 'curated' or 'generated'.

    Curated wins: if the source directory ships a ``README.md`` we serve it
    verbatim. Otherwise we generate a standardized MD from the contract.
    """
    entry = get_entry(kind, entry_id)
    if entry is None:
        raise KeyError(f"{kind}:{entry_id} not in registry")
    curated = _curated_md_path(kind, entry_id)
    if curated:
        return curated.read_text(encoding="utf-8"), "curated"
    return render_markdown(entry), "generated"


__all__ = [
    "render_markdown",
    "markdown_for",
    "CURATED_README_FILENAMES",
]


# Suppress unused-import warnings for stores referenced indirectly via hub
_ = (get_store, get_tool_store)
