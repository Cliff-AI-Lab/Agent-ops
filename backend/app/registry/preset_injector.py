"""PresetInjector — deterministically merge PresetBundle outputs into a
CodeArtifact based on RequirementSpec.product_type (Batch C++).

Workflow:
    1. Select bundles from PresetStore where:
         - bundle.activation_status == "active"
         - bundle.auto_inject is True (or bundle.bundle_id in explicit_ids)
         - spec.product_type ∈ bundle.applies_to
    2. Topo-sort by depends_on.
    3. For each bundle: render Jinja2 templates with {product, brand, type_specific}.
    4. Merge files into artifact (LLM wins on conflicts unless overwrite=True).
    5. Merge required_deps + required_dev_deps into artifact.dependencies.
    6. Apply entry_injections (best-effort string splicing on import lines + hint comments).

The injector is fully deterministic — no LLM call. This guarantees every
generated product ships with the platform's standard base modules regardless
of model behavior (blueprint §3.1 deterministic shell, probabilistic core).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from pydantic import BaseModel, ConfigDict, Field

from app.ontology import (
    CodeArtifact,
    FileEntry,
    PresetBundle,
    PresetEntryInjection,
    RequirementSpec,
    UIBlueprint,
)
from app.product_types import ProductType
from app.registry.preset_store import PresetStore, get_preset_store

_LOG = logging.getLogger("agent_ops.preset_injector")


class InjectionResult(BaseModel):
    """Outcome of a PresetInjector.inject call."""

    artifact: CodeArtifact
    injected_bundles: list[str] = Field(default_factory=list)
    skipped: list[tuple[str, str]] = Field(
        default_factory=list,
        description="(bundle_id, reason) pairs for bundles not injected.",
    )

    model_config = ConfigDict(arbitrary_types_allowed=True)


def select_bundles(
    store: PresetStore,
    product_type: ProductType,
    explicit_ids: list[str] | None = None,
) -> tuple[list[PresetBundle], list[tuple[str, str]]]:
    """Pick bundles applicable to this product, in dependency order."""
    skipped: list[tuple[str, str]] = []
    wanted: set[str] | None = set(explicit_ids) if explicit_ids is not None else None

    selected: list[PresetBundle] = []
    seen: set[str] = set()

    # Auto-inject path (default behavior)
    for bundle_id in store.auto_inject_ids():
        bundle = store.get(bundle_id)
        if bundle is None:
            continue
        if wanted is not None and bundle.bundle_id not in wanted:
            skipped.append((bundle.bundle_id, "not in explicit_ids"))
            continue
        if product_type not in bundle.applies_to:
            skipped.append((bundle.bundle_id, f"product_type={product_type} not in applies_to"))
            continue
        selected.append(bundle)
        seen.add(bundle.bundle_id)

    # Append explicit-only bundles (not auto_inject) that the caller asked for
    if wanted is not None:
        for bid in wanted:
            if bid in seen:
                continue
            bundle = store.get(bid)
            if bundle is None:
                skipped.append((bid, "bundle not registered"))
                continue
            if product_type not in bundle.applies_to:
                skipped.append((bid, f"product_type={product_type} not in applies_to"))
                continue
            selected.append(bundle)
            seen.add(bid)

    return selected, skipped


def render_preset_files(
    bundle: PresetBundle,
    source_dir: Path,
    context: dict[str, Any],
) -> list[FileEntry]:
    """Render each PresetFile via Jinja2 (or copy verbatim if is_template=False)."""
    # JSX/TSX uses `{{ }}` for inline objects, so we redefine Jinja2 variable
    # delimiters to `{[ var ]}` to avoid conflicts. Block tags `{% %}` and
    # comments `{# #}` are kept (they don't appear in JSX).
    # StrictUndefined: missing variables raise UndefinedError instead of
    # rendering empty strings (Batch C++.1, codex review feedback).
    env = Environment(
        loader=FileSystemLoader(str(source_dir)),
        variable_start_string="{[",
        variable_end_string="]}",
        block_start_string="{%",
        block_end_string="%}",
        comment_start_string="{#",
        comment_end_string="#}",
        autoescape=False,
        keep_trailing_newline=True,
        undefined=StrictUndefined,
    )
    rendered: list[FileEntry] = []
    for pf in bundle.files:
        if pf.is_template:
            tmpl = env.get_template(pf.template_path)
            content = tmpl.render(**context)
        else:
            src = source_dir / pf.template_path
            content = src.read_text(encoding="utf-8")
        rendered.append(FileEntry(path=pf.path, content=content))
    return rendered


def merge_files(
    artifact_files: list[FileEntry],
    preset_files: list[FileEntry],
    overwrite_paths: set[str],
) -> list[FileEntry]:
    """Merge with LLM-wins-by-default semantics."""
    by_path: dict[str, FileEntry] = {f.path: f for f in artifact_files}
    for pf in preset_files:
        if pf.path in by_path and pf.path not in overwrite_paths:
            continue  # LLM file wins
        by_path[pf.path] = pf
    return list(by_path.values())


def merge_deps(existing: dict[str, str], add: dict[str, str]) -> dict[str, str]:
    """Last-writer wins (presets are deterministic, conflicts unlikely)."""
    out = dict(existing)
    out.update(add)
    return out


def apply_entry_injections(
    files: list[FileEntry],
    injections: list[PresetEntryInjection],
) -> tuple[list[FileEntry], list[str]]:
    """Naive entry-file splicer: appends import lines + hint comments.

    Returns ``(new_files, missing_paths)`` so callers can surface the case
    where an entry file referenced by a preset's ``entry_injections`` does
    not exist in the artifact (otherwise the wiring silently fails — see
    codex review #2).

    Idempotent (Batch C++.1, codex review #1): an import line that already
    exists in the target file is skipped; a hint comment that already
    exists is skipped.

    Reliable AST rewriting of React/TS source is out of MVP scope; we emit
    PresetInjector hint comments so the developer (or the LLM during
    re-generation) can see what should wrap the root.
    """
    by_path = {f.path: f for f in files}
    grouped: dict[str, list[PresetEntryInjection]] = {}
    for inj in injections:
        grouped.setdefault(inj.file, []).append(inj)

    missing: list[str] = []
    for path, injs in grouped.items():
        if path not in by_path:
            missing.append(path)
            continue
        original = by_path[path].content
        new_content = _splice_entry(original, injs)
        by_path[path] = FileEntry(path=path, content=new_content)

    return list(by_path.values()), missing


def _splice_entry(src: str, injs: list[PresetEntryInjection]) -> str:
    """Idempotent entry splicer.

    Re-running on the same content with the same injections produces the
    same output (Batch C++.1, codex review #1): existing imports/hints are
    detected and not duplicated.
    """
    out = src
    existing_lines = set(out.split("\n"))

    # 1) imports — collect, dedupe across injections, skip if already present
    new_imports: list[str] = []
    seen: set[str] = set()
    for inj in injs:
        for line in inj.import_lines:
            if line in seen or line in existing_lines:
                continue
            seen.add(line)
            new_imports.append(line)
    if new_imports:
        lines = out.split("\n")
        last_import = -1
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("import ") or stripped.startswith("import{"):
                last_import = i
        insertion = "\n".join(new_imports)
        if last_import >= 0:
            lines.insert(last_import + 1, insertion)
        else:
            lines.insert(0, insertion)
        out = "\n".join(lines)

    # 2) hint comments — collect, dedupe, skip if already present in the file
    candidate_hints: list[str] = []
    for inj in injs:
        if inj.provider_open or inj.provider_close:
            candidate_hints.append(
                f"// PresetInjector hint: wrap root with {inj.provider_open}{{children}}{inj.provider_close}"
            )
        if inj.body_inject:
            candidate_hints.append(
                f"// PresetInjector hint: render {inj.body_inject} inside the root component"
            )
    new_hints: list[str] = []
    seen_hints: set[str] = set()
    for h in candidate_hints:
        if h in seen_hints or h in out:
            continue
        seen_hints.add(h)
        new_hints.append(h)
    if new_hints:
        out = "\n".join(new_hints) + "\n" + out

    return out


def _build_context(spec: RequirementSpec, blueprint: UIBlueprint | None) -> dict[str, Any]:
    brand = {"primary": "#5e81ac", "font": "Inter", "radius": "md"}
    if blueprint is not None:
        brand = {
            "primary": blueprint.brand.primary,
            "font": blueprint.brand.font,
            "radius": blueprint.brand.radius,
        }
    return {
        "product": {
            "name": spec.product_name,
            "type": spec.product_type,
            "users": list(spec.target_users),
        },
        "brand": brand,
        "type_specific": dict(spec.type_specific),
    }


def inject_presets(
    artifact: CodeArtifact,
    spec: RequirementSpec,
    blueprint: UIBlueprint | None = None,
    *,
    store: PresetStore | None = None,
    explicit_ids: list[str] | None = None,
) -> InjectionResult:
    """Inject preset bundles applicable to ``spec.product_type`` into ``artifact``."""
    s = store or get_preset_store()
    selected, skipped = select_bundles(s, spec.product_type, explicit_ids)
    if not selected:
        return InjectionResult(
            artifact=artifact,
            injected_bundles=[],
            skipped=skipped,
        )

    context = _build_context(spec, blueprint)
    new_files = list(artifact.files)
    new_deps = dict(artifact.dependencies)
    all_injections: list[PresetEntryInjection] = []
    injected_ids: list[str] = []

    for bundle in selected:
        source_dir = s.source_dir(bundle.bundle_id)
        if source_dir is None:
            skipped.append((bundle.bundle_id, "source_dir not registered"))
            continue
        try:
            preset_files = render_preset_files(bundle, source_dir, context)
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("preset %s render failed: %s", bundle.bundle_id, exc)
            skipped.append((bundle.bundle_id, f"render failed: {exc}"))
            continue
        overwrite_paths = {pf.path for pf in bundle.files if pf.overwrite}
        new_files = merge_files(new_files, preset_files, overwrite_paths)
        new_deps = merge_deps(new_deps, bundle.required_deps)
        all_injections.extend(bundle.entry_injections)
        injected_ids.append(bundle.bundle_id)

    new_files, missing_entry_paths = apply_entry_injections(new_files, all_injections)
    for path in missing_entry_paths:
        # Surface as a typed skip so callers (and trace listeners) see that
        # the entry-wiring step couldn't attach (codex review #2).
        skipped.append((
            f"entry:{path}",
            f"entry file {path} not found in artifact — preset wiring not applied",
        ))
        _LOG.warning("preset entry wiring skipped: %s missing in artifact", path)

    return InjectionResult(
        artifact=CodeArtifact(
            files=new_files,
            dependencies=new_deps,
            entrypoint=artifact.entrypoint,
        ),
        injected_bundles=injected_ids,
        skipped=skipped,
    )
