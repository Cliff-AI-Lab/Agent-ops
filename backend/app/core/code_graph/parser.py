"""TS/TSX/JS import-graph parser for a generated CodeArtifact (Batch C+++).

Approach: regex-based, no tree-sitter. Covers ~95% of relative + bare-package
imports for typical Vite/React projects we generate. Extracts:

- file nodes (every .ts/.tsx/.js/.jsx in the artifact)
- external nodes (one per imported npm package, e.g. 'react', '@/lib/utils')
- component nodes (uppercase default-exported / named-exported identifier)
- imports edges (file → file, file → external)
- contains edges (file → component)

Relative imports are resolved against the artifact's actual file paths
(supporting `../`, omitted extensions, and `index.{ts,tsx}` directory
imports). Unresolvable specifiers fall back to an "external" node tagged
with the original specifier.
"""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import PurePosixPath

from app.ontology import CodeArtifact
from app.ontology.objects.code_graph import CodeGraph, GraphEdge, GraphNode

_IMPORT_RE = re.compile(
    r"""^\s*import\s+(?:[^'"]*?\s+from\s+)?['"]([^'"]+)['"]""",
    re.MULTILINE,
)
_DYNAMIC_IMPORT_RE = re.compile(r"""import\s*\(\s*['"]([^'"]+)['"]\s*\)""")
_DEFAULT_EXPORT_RE = re.compile(
    r"export\s+default\s+(?:(?:async\s+)?function\s+([A-Z][A-Za-z0-9_]*)|class\s+([A-Z][A-Za-z0-9_]*))"
)
_NAMED_EXPORT_COMP_RE = re.compile(
    r"export\s+(?:(?:async\s+)?function|const|let|var)\s+([A-Z][A-Za-z0-9_]*)"
)

_PARSEABLE_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
_RESOLVE_EXTS = (".tsx", ".ts", ".jsx", ".js", ".mjs", ".cjs", ".json")


def parse_artifact(artifact: CodeArtifact) -> CodeGraph:
    """Build a CodeGraph from a CodeArtifact's TS/JS files."""
    file_paths = {f.path for f in artifact.files}
    nodes: dict[str, GraphNode] = {}
    edges: list[GraphEdge] = []

    # 1) File nodes
    for f in artifact.files:
        if not f.path.endswith(_PARSEABLE_EXTS):
            continue
        nodes[f.path] = GraphNode(
            id=f.path,
            label=PurePosixPath(f.path).name,
            type="file",
            file_path=f.path,
            metadata={
                "loc": f.content.count("\n") + 1,
                "dir": str(PurePosixPath(f.path).parent),
            },
        )

    # 2) Imports → edges
    edge_seq = 0
    for f in artifact.files:
        if f.path not in nodes:
            continue
        specs: list[str] = []
        specs.extend(m.group(1) for m in _IMPORT_RE.finditer(f.content))
        specs.extend(m.group(1) for m in _DYNAMIC_IMPORT_RE.finditer(f.content))
        for spec in specs:
            target = _resolve_relative(spec, f.path, file_paths)
            if target is None:
                # External package or unresolved alias — register as external node
                pkg = _package_root(spec)
                ext_id = f"ext:{pkg}"
                if ext_id not in nodes:
                    nodes[ext_id] = GraphNode(
                        id=ext_id,
                        label=pkg,
                        type="external",
                        metadata={"specifier": spec},
                    )
                target = ext_id
            edges.append(
                GraphEdge(
                    id=f"e{edge_seq}",
                    source=f.path,
                    target=target,
                    type="imports",
                )
            )
            edge_seq += 1

    # 3) Component nodes (uppercase exports inside .tsx/.jsx)
    for f in artifact.files:
        if not f.path.endswith((".tsx", ".jsx")):
            continue
        if f.path not in nodes:
            continue
        comp_names: set[str] = set()
        for m in _DEFAULT_EXPORT_RE.finditer(f.content):
            name = m.group(1) or m.group(2)
            if name:
                comp_names.add(name)
        for m in _NAMED_EXPORT_COMP_RE.finditer(f.content):
            comp_names.add(m.group(1))
        for name in sorted(comp_names):
            comp_id = f"comp:{f.path}:{name}"
            nodes[comp_id] = GraphNode(
                id=comp_id,
                label=name,
                type="component",
                file_path=f.path,
            )
            edges.append(
                GraphEdge(
                    id=f"e{edge_seq}",
                    source=f.path,
                    target=comp_id,
                    type="contains",
                )
            )
            edge_seq += 1

    # 4) Stats summary for quick UI badge
    stats: dict[str, int] = defaultdict(int)
    for n in nodes.values():
        stats[f"node:{n.type}"] += 1
    for e in edges:
        stats[f"edge:{e.type}"] += 1
    stats["total_nodes"] = len(nodes)
    stats["total_edges"] = len(edges)

    return CodeGraph(
        nodes=list(nodes.values()),
        edges=edges,
        stats=dict(stats),
    )


def _resolve_relative(spec: str, source_path: str, all_paths: set[str]) -> str | None:
    """Resolve a relative import specifier to an actual file path; None if unresolvable
    or external package."""
    if not spec.startswith("."):
        return None
    base = PurePosixPath(source_path).parent
    raw = (base / spec).as_posix() if str(base) != "." else spec.lstrip("./")
    parts: list[str] = []
    for part in raw.split("/"):
        if part == "..":
            if parts:
                parts.pop()
        elif part and part != ".":
            parts.append(part)
    target = "/".join(parts)
    if not target:
        return None

    if target in all_paths:
        return target
    # Try with each extension if not already extensioned
    if not target.endswith(_RESOLVE_EXTS):
        for ext in _RESOLVE_EXTS:
            cand = target + ext
            if cand in all_paths:
                return cand
            cand_index = f"{target}/index{ext}"
            if cand_index in all_paths:
                return cand_index
    return None


def _package_root(spec: str) -> str:
    """For 'react' return 'react'; for '@scope/pkg/sub' return '@scope/pkg'."""
    if spec.startswith("@"):
        parts = spec.split("/")
        return "/".join(parts[:2]) if len(parts) >= 2 else spec
    return spec.split("/")[0]
