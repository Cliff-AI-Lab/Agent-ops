"""PresetStore + YAML loader for the standardized front-end preset bundles
(Batch C++).

A preset directory looks like::

    presets/
        i18n/
            preset.yaml
            template/
                src/lib/i18n.ts.j2
                ...

``preset.yaml`` is a ``PresetBundle`` document. Template paths inside
``files[]`` are resolved relative to the preset's directory.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path

import yaml

from app.ontology import PresetBundle

_LOG = logging.getLogger("agent_ops.registry.presets")


def load_preset_from_dir(preset_dir: Path) -> PresetBundle:
    """Load ``preset_dir/preset.yaml`` and validate it as a PresetBundle."""
    yaml_path = preset_dir / "preset.yaml"
    if not yaml_path.is_file():
        raise FileNotFoundError(f"missing preset.yaml in {preset_dir}")
    with yaml_path.open(encoding="utf-8") as fp:
        data = yaml.safe_load(fp) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{yaml_path}: top-level YAML must be a mapping")
    return PresetBundle.model_validate(data)


def load_presets_from_dir(presets_root: Path) -> list[tuple[PresetBundle, Path]]:
    """Each immediate child directory of ``presets_root`` containing a
    ``preset.yaml`` is treated as one PresetBundle.

    Returns ``(bundle, source_dir)`` pairs so the store can record each
    bundle's source directory directly (no fragile reliance on
    ``directory_name == bundle_id``).
    """
    if not presets_root.is_dir():
        _LOG.warning("presets dir not found: %s", presets_root)
        return []
    out: list[tuple[PresetBundle, Path]] = []
    for child in sorted(presets_root.iterdir()):
        if not child.is_dir():
            continue
        if not (child / "preset.yaml").is_file():
            continue
        try:
            bundle = load_preset_from_dir(child)
            out.append((bundle, child))
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("failed to load preset %s: %s", child, exc)
    return out


class PresetStore:
    """In-memory registry of PresetBundle (keyed by bundle_id)."""

    def __init__(self) -> None:
        self._bundles: dict[str, PresetBundle] = {}
        self._dirs: dict[str, Path] = {}

    def register(self, bundle: PresetBundle, source_dir: Path | None = None) -> None:
        self._bundles[bundle.bundle_id] = bundle
        if source_dir is not None:
            self._dirs[bundle.bundle_id] = source_dir

    def register_many(self, bundles: Iterable[tuple[PresetBundle, Path]]) -> int:
        n = 0
        for b, d in bundles:
            self.register(b, d)
            n += 1
        return n

    def get(self, bundle_id: str) -> PresetBundle | None:
        return self._bundles.get(bundle_id)

    def source_dir(self, bundle_id: str) -> Path | None:
        return self._dirs.get(bundle_id)

    def list(self, activation_status: str | None = None) -> list[PresetBundle]:
        items = list(self._bundles.values())
        if activation_status:
            items = [b for b in items if b.activation_status == activation_status]
        return sorted(items, key=lambda b: b.bundle_id)

    def auto_inject_ids(self) -> list[str]:
        """Active + auto_inject bundles in topological order.

        Strict closure rules (Batch C++.1, addressing codex review #3):

        - Only ACTIVE + auto_inject bundles enter the result.
        - Dependencies on ACTIVE bundles get traversed and emitted **in front of** their dependents.
        - Dependencies on INACTIVE / non-auto_inject / missing bundles are
          logged as warnings and skipped — the dependent is still emitted
          (best-effort), but the inactive dep does NOT leak into the result.
        - Cycles are detected via a ``visiting`` set; the second-entry edge
          is logged and skipped (no infinite recursion).
        """
        active_ids = {
            b.bundle_id for b in self._bundles.values()
            if b.activation_status == "active" and b.auto_inject
        }
        order: list[str] = []
        visited: set[str] = set()
        visiting: set[str] = set()  # cycle detection

        def visit(bid: str) -> None:
            if bid in visited:
                return
            if bid in visiting:
                _LOG.warning("preset dependency cycle detected at %s", bid)
                return
            if bid not in self._bundles:
                _LOG.warning("preset dependency missing: %s", bid)
                return
            if bid not in active_ids:
                _LOG.warning(
                    "preset dependency %s is inactive or auto_inject=False — skipped",
                    bid,
                )
                return
            visiting.add(bid)
            for dep in self._bundles[bid].depends_on:
                visit(dep)
            visiting.discard(bid)
            visited.add(bid)
            order.append(bid)

        for bid in sorted(active_ids):
            visit(bid)
        return order

    def __len__(self) -> int:
        return len(self._bundles)


_preset_store: PresetStore | None = None


def get_preset_store() -> PresetStore:
    global _preset_store
    if _preset_store is None:
        _preset_store = PresetStore()
    return _preset_store


def reset_preset_store() -> None:
    global _preset_store
    _preset_store = PresetStore()


def default_presets_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "presets"


def bootstrap_presets(presets_dir: Path | None = None) -> int:
    root = presets_dir or default_presets_dir()
    pairs = load_presets_from_dir(root)
    n = get_preset_store().register_many(pairs)
    _LOG.info("registered %d presets from %s", n, root)
    return n
