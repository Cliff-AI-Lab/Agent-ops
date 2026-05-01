"""PresetBundle + PresetStore + PresetInjector tests (Batch C++)."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.ontology import (
    BrandTokens,
    CodeArtifact,
    FileEntry,
    PageBlueprint,
    PresetBundle,
    RequirementSpec,
    UIBlueprint,
)
from app.registry.preset_injector import (
    InjectionResult,
    apply_entry_injections,
    inject_presets,
    merge_deps,
    merge_files,
    select_bundles,
)
from app.registry.preset_store import (
    PresetStore,
    bootstrap_presets,
    default_presets_dir,
    load_preset_from_dir,
    load_presets_from_dir,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PRESETS_DIR = PROJECT_ROOT / "presets"


@pytest.fixture
def fresh_store() -> PresetStore:
    return PresetStore()


@pytest.fixture
def i18n_preset() -> PresetBundle:
    return load_preset_from_dir(PRESETS_DIR / "i18n")


@pytest.fixture
def sample_app_spec() -> RequirementSpec:
    return RequirementSpec(
        product_name="DemoApp",
        product_type="app",
        target_users=["team"],
        core_pages=["home", "tasks"],
    )


@pytest.fixture
def sample_blueprint() -> UIBlueprint:
    return UIBlueprint(
        pages=[
            PageBlueprint(route="/", title="Home", components=["hero"], data_fields=["title"]),
        ],
        brand=BrandTokens(primary="#5e81ac", font="Inter", radius="md"),
    )


@pytest.fixture
def sample_artifact() -> CodeArtifact:
    return CodeArtifact(
        files=[
            FileEntry(path="src/App.tsx", content="import './App.css';\nexport default function App() {\n  return <div>hello</div>;\n}\n"),
            FileEntry(path="src/main.tsx", content="import App from './App';\n"),
            FileEntry(path="package.json", content='{"name":"x","version":"0.1.0"}'),
        ],
        dependencies={"react": "^18.3.1"},
        entrypoint="src/main.tsx",
    )


# ---------- YAML loading ----------
def test_load_i18n_preset_from_dir(i18n_preset):
    assert i18n_preset.bundle_id == "i18n"
    assert i18n_preset.activation_status == "active"
    assert i18n_preset.auto_inject is True
    assert "app" in i18n_preset.applies_to
    assert any(f.path.endswith("i18n.tsx") for f in i18n_preset.files)
    assert i18n_preset.entry_injections, "i18n must declare entry injections"


def test_load_all_presets_from_dir():
    pairs = load_presets_from_dir(PRESETS_DIR)
    ids = {b.bundle_id for b, _ in pairs}
    assert "i18n" in ids
    # The loader records each bundle's source dir directly (Batch C++.1).
    by_id = {b.bundle_id: src for b, src in pairs}
    assert by_id["i18n"] == PRESETS_DIR / "i18n"


def test_bootstrap_registers_presets(monkeypatch):
    from app.registry import preset_store as ps_mod

    monkeypatch.setattr(ps_mod, "_preset_store", None)
    n = bootstrap_presets()
    assert n >= 1
    store = ps_mod.get_preset_store()
    assert store.get("i18n") is not None
    assert store.source_dir("i18n") == default_presets_dir() / "i18n"


# ---------- Store CRUD ----------
def test_store_register_get_list(fresh_store: PresetStore, i18n_preset):
    fresh_store.register(i18n_preset, source_dir=PRESETS_DIR / "i18n")
    assert fresh_store.get("i18n") is i18n_preset
    assert fresh_store.get("missing") is None
    assert fresh_store.source_dir("i18n") == PRESETS_DIR / "i18n"
    assert len(fresh_store.list(activation_status="active")) == 1
    assert len(fresh_store.list(activation_status="draft")) == 0
    assert "i18n" in fresh_store.auto_inject_ids()


def test_store_topo_sort(fresh_store: PresetStore, i18n_preset):
    fresh_store.register(i18n_preset, source_dir=PRESETS_DIR / "i18n")
    # Synthetic bundle that depends on i18n
    composite = PresetBundle(
        bundle_id="settings_drawer",
        version="0.1.0",
        name="Settings Drawer",
        description="Combines theme + i18n",
        depends_on=["i18n"],
    )
    fresh_store.register(composite, source_dir=PRESETS_DIR / "settings_drawer")
    order = fresh_store.auto_inject_ids()
    assert order.index("i18n") < order.index("settings_drawer")


# ---------- select_bundles ----------
def test_select_bundles_filters_by_product_type(fresh_store: PresetStore, i18n_preset):
    fresh_store.register(i18n_preset, source_dir=PRESETS_DIR / "i18n")
    selected, skipped = select_bundles(fresh_store, "app")
    assert [b.bundle_id for b in selected] == ["i18n"]
    assert skipped == []

    selected, skipped = select_bundles(fresh_store, "tool")
    assert selected == []
    assert any("not in applies_to" in r for _, r in skipped)


def test_select_bundles_explicit_ids(fresh_store: PresetStore, i18n_preset):
    fresh_store.register(i18n_preset, source_dir=PRESETS_DIR / "i18n")
    selected, _ = select_bundles(fresh_store, "app", explicit_ids=["i18n"])
    assert [b.bundle_id for b in selected] == ["i18n"]
    selected, skipped = select_bundles(fresh_store, "app", explicit_ids=["nonexistent"])
    assert selected == []
    assert any("not registered" in r for _, r in skipped)


# ---------- merge_files ----------
def test_merge_files_llm_wins_by_default(sample_artifact):
    preset_files = [FileEntry(path="src/App.tsx", content="// preset wins"), FileEntry(path="src/lib/i18n.tsx", content="// new")]
    merged = merge_files(sample_artifact.files, preset_files, overwrite_paths=set())
    paths = {f.path: f.content for f in merged}
    assert "import './App.css'" in paths["src/App.tsx"]  # original preserved
    assert paths["src/lib/i18n.tsx"] == "// new"


def test_merge_files_overwrite_path_wins(sample_artifact):
    preset_files = [FileEntry(path="src/App.tsx", content="// preset overrides")]
    merged = merge_files(sample_artifact.files, preset_files, overwrite_paths={"src/App.tsx"})
    paths = {f.path: f.content for f in merged}
    assert paths["src/App.tsx"] == "// preset overrides"


def test_merge_deps():
    out = merge_deps({"a": "1", "b": "2"}, {"b": "3", "c": "4"})
    assert out == {"a": "1", "b": "3", "c": "4"}


# ---------- entry_injections ----------
def test_apply_entry_injections_appends_imports(sample_artifact, i18n_preset):
    files = list(sample_artifact.files)
    new_files, missing = apply_entry_injections(files, i18n_preset.entry_injections)
    assert missing == []
    app = next(f for f in new_files if f.path == "src/App.tsx")
    assert "import { I18nProvider }" in app.content
    assert "PresetInjector hint: wrap root with <I18nProvider>" in app.content


# ---------- Batch C++.1 — codex review fixes ----------
def test_apply_entry_injections_idempotent(sample_artifact, i18n_preset):
    """Running the splicer twice yields the same result (codex review #1)."""
    files = list(sample_artifact.files)
    once, _ = apply_entry_injections(files, i18n_preset.entry_injections)
    twice, _ = apply_entry_injections(once, i18n_preset.entry_injections)
    once_app = next(f.content for f in once if f.path == "src/App.tsx")
    twice_app = next(f.content for f in twice if f.path == "src/App.tsx")
    assert once_app == twice_app, "splicer must be idempotent — second run should not duplicate imports/hints"


def test_apply_entry_injections_reports_missing_entry(i18n_preset):
    """If the target entry file is absent, surface it via missing list (codex review #2)."""
    files = [FileEntry(path="src/main.tsx", content="// no App.tsx here")]
    new_files, missing = apply_entry_injections(files, i18n_preset.entry_injections)
    assert "src/App.tsx" in missing
    # main.tsx is untouched
    main = next(f for f in new_files if f.path == "src/main.tsx")
    assert main.content == "// no App.tsx here"


def test_inject_presets_records_missing_entry_in_skipped(
    fresh_store, i18n_preset, sample_app_spec
):
    """inject_presets surfaces missing entry files in InjectionResult.skipped."""
    fresh_store.register(i18n_preset, source_dir=PRESETS_DIR / "i18n")
    artifact = CodeArtifact(
        files=[FileEntry(path="src/main.tsx", content="// no App.tsx")],
        dependencies={},
        entrypoint="src/main.tsx",
    )
    result = inject_presets(artifact, sample_app_spec, store=fresh_store)
    assert any(bid.startswith("entry:") and "src/App.tsx" in reason for bid, reason in result.skipped)
    # i18n is still listed as injected (its files were merged) — only the wiring step skipped.
    assert "i18n" in result.injected_bundles


def test_auto_inject_ids_skips_inactive_dependency(fresh_store):
    """A dependent bundle is emitted; its inactive dep is skipped (codex review #3)."""
    inactive = PresetBundle(
        bundle_id="inactive_dep",
        version="0.1.0",
        name="Inactive",
        description="should not be auto-injected",
        activation_status="draft",
        auto_inject=False,
    )
    consumer = PresetBundle(
        bundle_id="consumer",
        version="0.1.0",
        name="Consumer",
        description="depends on an inactive bundle",
        depends_on=["inactive_dep"],
        activation_status="active",
        auto_inject=True,
    )
    fresh_store.register(inactive, source_dir=PRESETS_DIR)
    fresh_store.register(consumer, source_dir=PRESETS_DIR)
    order = fresh_store.auto_inject_ids()
    assert "consumer" in order
    assert "inactive_dep" not in order


def test_auto_inject_ids_handles_cycle(fresh_store, caplog):
    """A → B → A cycle does not infinite-loop and gets logged."""
    a = PresetBundle(
        bundle_id="cycle_a", version="0.1.0", name="A", description="cycle",
        depends_on=["cycle_b"], activation_status="active",
    )
    b = PresetBundle(
        bundle_id="cycle_b", version="0.1.0", name="B", description="cycle",
        depends_on=["cycle_a"], activation_status="active",
    )
    fresh_store.register(a, source_dir=PRESETS_DIR)
    fresh_store.register(b, source_dir=PRESETS_DIR)
    with caplog.at_level("WARNING", logger="agent_ops.registry.presets"):
        order = fresh_store.auto_inject_ids()
    assert set(order) == {"cycle_a", "cycle_b"}
    assert any("cycle" in rec.message.lower() for rec in caplog.records)


def test_jinja_strict_undefined_surfaces_missing_var(fresh_store, sample_artifact, sample_app_spec):
    """A preset whose template references an undefined variable should be
    skipped with a render error rather than rendering an empty string
    (codex review: StrictUndefined)."""
    bad_dir = PRESETS_DIR / "_bad_var"
    bad_dir.mkdir(parents=True, exist_ok=True)
    (bad_dir / "preset.yaml").write_text(
        "bundle_id: bad_var\nversion: 0.0.1\nname: Bad Var\ndescription: missing var\n"
        "framework: react-vite\nauto_inject: true\napplies_to: [app]\n"
        "files:\n  - {path: src/lib/x.ts, template_path: t.ts.j2, is_template: true}\n"
        "activation_status: active\n",
        encoding="utf-8",
    )
    (bad_dir / "t.ts.j2").write_text("export const X = '{[ does.not.exist ]}';\n", encoding="utf-8")
    try:
        bundle = next(b for b, _ in load_presets_from_dir(PRESETS_DIR) if b.bundle_id == "bad_var")
        fresh_store.register(bundle, source_dir=bad_dir)
        result = inject_presets(sample_artifact, sample_app_spec, store=fresh_store)
        assert "bad_var" not in result.injected_bundles
        assert any(bid == "bad_var" and "render failed" in reason for bid, reason in result.skipped)
    finally:
        # Cleanup so other tests don't see the synthetic preset
        (bad_dir / "preset.yaml").unlink()
        (bad_dir / "t.ts.j2").unlink()
        bad_dir.rmdir()


# ---------- end-to-end inject_presets ----------
def test_inject_presets_full_pipeline(
    fresh_store, i18n_preset, sample_app_spec, sample_blueprint, sample_artifact
):
    fresh_store.register(i18n_preset, source_dir=PRESETS_DIR / "i18n")
    result = inject_presets(
        sample_artifact,
        sample_app_spec,
        sample_blueprint,
        store=fresh_store,
    )
    assert isinstance(result, InjectionResult)
    assert result.injected_bundles == ["i18n"]
    paths = {f.path: f.content for f in result.artifact.files}
    # i18n produced files
    assert "src/lib/i18n.tsx" in paths
    assert "src/components/LanguageSwitcher.tsx" in paths
    assert "src/locales/zh.json" in paths
    assert "src/locales/en.json" in paths
    # original LLM file preserved
    assert "import './App.css'" in paths["src/App.tsx"]
    # entry imports appended
    assert "import { I18nProvider }" in paths["src/App.tsx"]
    # variables substituted (brand.primary = #5e81ac via {[ brand.primary ]})
    assert "#5e81ac" in paths["src/components/LanguageSwitcher.tsx"]
    # product.name interpolated into storage key
    assert "DemoApp.locale" in paths["src/lib/i18n.tsx"]


def test_inject_presets_skipped_for_tool_product(
    fresh_store, i18n_preset, sample_artifact
):
    fresh_store.register(i18n_preset, source_dir=PRESETS_DIR / "i18n")
    spec = RequirementSpec(
        product_name="MyTool",
        product_type="tool",
        target_users=["dev"],
        core_pages=["main"],
    )
    result = inject_presets(sample_artifact, spec, store=fresh_store)
    assert result.injected_bundles == []
    assert any(bid == "i18n" for bid, _ in result.skipped)
    # artifact unchanged
    assert {f.path for f in result.artifact.files} == {f.path for f in sample_artifact.files}
