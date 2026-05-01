"""CodeGraph parser tests (Batch C+++)."""
from __future__ import annotations

from app.core.code_graph import parse_artifact
from app.ontology import CodeArtifact, FileEntry


def _make_artifact(files: dict[str, str]) -> CodeArtifact:
    return CodeArtifact(
        files=[FileEntry(path=p, content=c) for p, c in files.items()],
        dependencies={},
        entrypoint=next(iter(files)),
    )


def test_parser_picks_up_file_nodes():
    art = _make_artifact({
        "src/App.tsx": "import React from 'react';\nexport default function App() { return null; }\n",
        "src/main.tsx": "import App from './App';\n",
        "package.json": "{}",
    })
    g = parse_artifact(art)
    file_ids = {n.id for n in g.nodes if n.type == "file"}
    assert file_ids == {"src/App.tsx", "src/main.tsx"}
    # package.json is not parseable → not a node
    assert "package.json" not in {n.id for n in g.nodes}


def test_parser_resolves_relative_imports():
    art = _make_artifact({
        "src/App.tsx": "import { Button } from './components/Button';\n",
        "src/components/Button.tsx": "export const Button = () => null;\n",
    })
    g = parse_artifact(art)
    edge_targets = {(e.source, e.target) for e in g.edges if e.type == "imports"}
    assert ("src/App.tsx", "src/components/Button.tsx") in edge_targets


def test_parser_resolves_relative_imports_with_index_file():
    art = _make_artifact({
        "src/App.tsx": "import { x } from './lib';\n",
        "src/lib/index.ts": "export const x = 1;\n",
    })
    g = parse_artifact(art)
    targets = {e.target for e in g.edges if e.source == "src/App.tsx"}
    assert "src/lib/index.ts" in targets


def test_parser_resolves_parent_relative_imports():
    art = _make_artifact({
        "src/components/A.tsx": "import { util } from '../lib/util';\n",
        "src/lib/util.ts": "export const util = 1;\n",
    })
    g = parse_artifact(art)
    targets = {e.target for e in g.edges if e.source == "src/components/A.tsx"}
    assert "src/lib/util.ts" in targets


def test_parser_groups_external_packages():
    art = _make_artifact({
        "src/App.tsx": (
            "import React from 'react';\n"
            "import { useState } from 'react';\n"
            "import { Button } from '@radix-ui/react-button';\n"
            "import deep from 'lodash/deep';\n"
        ),
    })
    g = parse_artifact(art)
    ext_labels = {n.label for n in g.nodes if n.type == "external"}
    # "react" coalesced; @radix-ui/react-button kept as scope+pkg; lodash from lodash/deep
    assert "react" in ext_labels
    assert "@radix-ui/react-button" in ext_labels
    assert "lodash" in ext_labels


def test_parser_extracts_default_export_components():
    art = _make_artifact({
        "src/Button.tsx": (
            "import React from 'react';\n"
            "export default function Button() { return null; }\n"
        ),
    })
    g = parse_artifact(art)
    component_nodes = [n for n in g.nodes if n.type == "component"]
    assert len(component_nodes) == 1
    assert component_nodes[0].label == "Button"
    contains_edges = [e for e in g.edges if e.type == "contains"]
    assert any(e.source == "src/Button.tsx" for e in contains_edges)


def test_parser_extracts_named_uppercase_components():
    art = _make_artifact({
        "src/Cards.tsx": (
            "export function CardA() { return null; }\n"
            "export const CardB = () => null;\n"
            "export function helperFn() { return 0; }\n"
        ),
    })
    g = parse_artifact(art)
    comps = sorted(n.label for n in g.nodes if n.type == "component")
    assert comps == ["CardA", "CardB"]
    # lowercase helperFn must NOT be classified as a component
    assert "helperFn" not in comps


def test_parser_handles_dynamic_import():
    art = _make_artifact({
        "src/App.tsx": "const m = import('./lazy');\n",
        "src/lazy.ts": "export default 1;\n",
    })
    g = parse_artifact(art)
    targets = {e.target for e in g.edges if e.source == "src/App.tsx"}
    assert "src/lazy.ts" in targets


def test_parser_stats_summary():
    art = _make_artifact({
        "src/A.tsx": "import { B } from './B';\nexport default function A() { return null; }\n",
        "src/B.tsx": "import React from 'react';\nexport default function B() { return null; }\n",
    })
    g = parse_artifact(art)
    assert g.stats["total_nodes"] == len(g.nodes)
    assert g.stats["total_edges"] == len(g.edges)
    assert g.stats["node:file"] == 2
    assert g.stats["node:component"] == 2
    assert g.stats["node:external"] == 1  # react
    assert g.stats["edge:imports"] >= 2
    assert g.stats["edge:contains"] == 2


def test_parser_unresolvable_relative_falls_back_to_external():
    art = _make_artifact({
        "src/App.tsx": "import { x } from './missing';\n",
    })
    g = parse_artifact(art)
    # './missing' can't be resolved; should NOT crash, and should appear as external
    ext = [n for n in g.nodes if n.type == "external"]
    assert any("./missing" in n.metadata.get("specifier", "") or n.label.endswith("missing") for n in ext)


def test_parser_skips_non_js_files():
    art = _make_artifact({
        "README.md": "# hi",
        "package.json": "{}",
        "tailwind.config.ts": "export default {};",
    })
    g = parse_artifact(art)
    file_ids = {n.id for n in g.nodes if n.type == "file"}
    assert file_ids == {"tailwind.config.ts"}
