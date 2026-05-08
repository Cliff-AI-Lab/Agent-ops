"""DifyCompilerImpl V2 - emits Dify-compatible YAML (DSL v0.4.0+) from ResolvedDAG.

V2 (Phase 8 Day 1): rewrite to match real Dify export schema reverse-engineered
from ``api/services/app_dsl_service.py`` and golden sample
``scripts/stress-test/setup/dsl/workflow_llm.yml``.

Top-level structure:
    version: "0.4.0"   # DSL version; 0.6.0 (current main) accepts 0.4.0
    kind: app
    app: { name, mode, icon, icon_background, description, use_icon_as_answer_icon }
    dependencies: []
    workflow:
      conversation_variables: []
      environment_variables: []
      features: {}
      graph:
        edges: [...]
        nodes: [...]
        viewport: {x, y, zoom}

Per-node structure (every node has top-level type=custom, real type in data.type):
    - data: { type: start|llm|code|http-request|end, title, desc, ... }
      id: '<string_id>'
      type: custom
      position: {x, y}
      positionAbsolute: {x, y}
      width: 244
      height: 90
      sourcePosition: right
      targetPosition: left
      selected: false

PURE TEMPLATE-BASED: NO LLM call here. Determinism is the entire point.
Same ResolvedDAG -> same YAML output every time.

Phase 8 Day 2-3 will refine per-atom projections via atom.projections.dify.template.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import jinja2
import yaml

from app.core.pipelines.factory.ir import ResolvedDAG, ResolvedEdge, ResolvedNode
from app.core.trace.bus import emit
from app.registry.atom_loader import AtomDef, AtomLoaderImpl

logger = logging.getLogger(__name__)


_DIFY_DSL_VERSION = "0.4.0"

# subcategory -> Dify node data.type
_DATA_TYPE_MAP = {
    "DB": "code",
    "HTTP": "http-request",
    "LLM": "llm",
    "Notify": "http-request",
    "Schedule": "code",
    "OCR": "code",
    "TTS": "code",
    "ASR": "code",
    "VectorDB": "code",
    "Embedding": "code",
    "DocParser": "code",
    "Chart": "code",
    "WebSearch": "code",
}

_NODE_X_BASE = 30
_NODE_X_STEP = 304
_NODE_Y = 245
_NODE_W = 244
_NODE_H = 90


class _TemplateHelpers:
    """Jinja2 context helpers for atom Dify templates.

    Atom yaml templates reference these as ``helpers.xxx``. Centralized so
    every atom emits the same shape (model placeholder, system prompt,
    code-node variables) without duplicating logic in 5 yaml files.
    """

    @staticmethod
    def ruidong_model_placeholder(task_type: str | None, size: str | None) -> str:
        task_key = (task_type or "").replace(" ", "_").replace("/", "")
        return f"${{RUIDONG_MODEL_FOR_{task_key}_{size or ''}}}"

    @staticmethod
    def uuid() -> str:
        return str(uuid.uuid4())

    @staticmethod
    def system_prompt(node: ResolvedNode) -> str:
        if node.prompt_id:
            return f"prompt_id={node.prompt_id} (resolved at runtime via prompt registry)"
        return ""

    @staticmethod
    def code_variables(node: ResolvedNode) -> list[dict[str, Any]]:
        return [{"variable": k, "value_selector": []} for k in node.inputs.keys()]


_HELPERS = _TemplateHelpers()


class DifyCompilerImpl:
    """V2 compiler. Output is Dify-compatible YAML (DSL v0.4.0+).

    The synthetic Start/End nodes are auto-prepended/appended because Dify
    requires every workflow to begin with a `start` node and end with `end`.
    """

    def __init__(self, atom_loader: AtomLoaderImpl | None = None) -> None:
        self._atoms_by_id: dict[str, AtomDef] = {}
        if atom_loader is not None:
            self._loader = atom_loader

    def index(self, atoms: dict[str, AtomDef]) -> None:
        self._atoms_by_id = atoms

    def compile(self, dag: ResolvedDAG) -> str:
        if dag.target not in ("dify", "hybrid"):
            raise ValueError(
                f"DifyCompilerImpl only handles dify/hybrid targets, got {dag.target}"
            )

        emit(
            "L3",
            "DifyCompiler",
            "compile_start",
            f"target={dag.target} nodes={len(dag.nodes)}",
        )

        scoped_nodes = self._scope_nodes(dag)
        scoped_edges = self._scope_edges(dag, scoped_nodes)

        rendered_nodes: list[dict[str, Any]] = []
        rendered_edges: list[dict[str, Any]] = []

        start_id = "node_start"
        end_id = "node_end"

        rendered_nodes.append(self._build_start_node(start_id, position_index=0))

        for i, n in enumerate(scoped_nodes, start=1):
            rendered_nodes.append(self._render_node(n, position_index=i))

        rendered_nodes.append(
            self._build_end_node(
                end_id,
                upstream_id=scoped_nodes[-1].id if scoped_nodes else start_id,
                position_index=len(scoped_nodes) + 1,
            )
        )

        if scoped_nodes:
            first = scoped_nodes[0]
            last = scoped_nodes[-1]
            rendered_edges.append(
                self._build_edge(start_id, first.id, "start", self._data_type(first))
            )
            for e in scoped_edges:
                src = next((n for n in scoped_nodes if n.id == e.from_node), None)
                tgt = next((n for n in scoped_nodes if n.id == e.to_node), None)
                if src and tgt:
                    rendered_edges.append(
                        self._build_edge(
                            e.from_node,
                            e.to_node,
                            self._data_type(src),
                            self._data_type(tgt),
                        )
                    )
            rendered_edges.append(
                self._build_edge(last.id, end_id, self._data_type(last), "end")
            )
        else:
            rendered_edges.append(self._build_edge(start_id, end_id, "start", "end"))

        app_dict: dict[str, Any] = {
            "version": _DIFY_DSL_VERSION,
            "kind": "app",
            "app": {
                "name": f"specimen_{dag.intent_ref[:30]}",
                "mode": "workflow",
                "icon": "\U0001F916",
                "icon_background": "#FFEAD5",
                "description": (
                    f"Generated by agent-ops V2.0.0 factory; "
                    f"target={dag.target} pattern={dag.pattern_id or 'none'}"
                ),
                "use_icon_as_answer_icon": False,
            },
            "dependencies": [],
            "workflow": {
                "conversation_variables": [],
                "environment_variables": [],
                "features": {},
                "graph": {
                    "edges": rendered_edges,
                    "nodes": rendered_nodes,
                    "viewport": {"x": 0, "y": 0, "zoom": 0.7},
                },
            },
            "_factory_metadata": {
                "intent_ref": dag.intent_ref,
                "pattern_id": dag.pattern_id,
                "target": dag.target,
                "issues": dag.issues,
            },
        }
        out = yaml.safe_dump(
            app_dict, allow_unicode=True, sort_keys=False, default_flow_style=False
        )
        emit(
            "L3",
            "DifyCompiler",
            "compile_done",
            f"yaml_chars={len(out)} nodes={len(rendered_nodes)}",
        )
        return out

    def _scope_nodes(self, dag: ResolvedDAG) -> list[ResolvedNode]:
        if dag.target == "dify":
            return list(dag.nodes)
        if dag.target_split:
            ids = set(dag.target_split.get("dify_nodes", []))
            return [n for n in dag.nodes if n.id in ids]
        return list(dag.nodes)

    def _scope_edges(
        self, dag: ResolvedDAG, scoped_nodes: list[ResolvedNode]
    ) -> list[ResolvedEdge]:
        ids = {n.id for n in scoped_nodes}
        return [e for e in dag.edges if e.from_node in ids and e.to_node in ids]

    def _data_type(self, node: ResolvedNode) -> str:
        atom = self._atoms_by_id.get(node.asset_id)
        sub = atom.subcategory if atom else "Unknown"
        return _DATA_TYPE_MAP.get(sub, "code")

    def _node_position(self, index: int) -> dict[str, int]:
        return {"x": _NODE_X_BASE + index * _NODE_X_STEP, "y": _NODE_Y}

    def _build_start_node(self, node_id: str, position_index: int) -> dict[str, Any]:
        pos = self._node_position(position_index)
        return {
            "id": node_id,
            "type": "custom",
            "position": pos,
            "positionAbsolute": dict(pos),
            "width": _NODE_W,
            "height": _NODE_H,
            "sourcePosition": "right",
            "targetPosition": "left",
            "selected": False,
            "data": {
                "type": "start",
                "title": "Start",
                "desc": "",
                "selected": False,
                "variables": [],
            },
        }

    def _build_end_node(
        self, node_id: str, upstream_id: str, position_index: int
    ) -> dict[str, Any]:
        pos = self._node_position(position_index)
        return {
            "id": node_id,
            "type": "custom",
            "position": pos,
            "positionAbsolute": dict(pos),
            "width": _NODE_W,
            "height": _NODE_H,
            "sourcePosition": "right",
            "targetPosition": "left",
            "selected": False,
            "data": {
                "type": "end",
                "title": "End",
                "desc": "",
                "selected": False,
                "outputs": [
                    {
                        "value_selector": [upstream_id, "text"],
                        "value_type": "string",
                        "variable": "result",
                    }
                ],
            },
        }

    def _build_edge(
        self, source_id: str, target_id: str, source_type: str, target_type: str
    ) -> dict[str, Any]:
        return {
            "id": f"{source_id}-source-{target_id}-target",
            "source": source_id,
            "sourceHandle": "source",
            "target": target_id,
            "targetHandle": "target",
            "type": "custom",
            "zIndex": 0,
            "data": {
                "isInIteration": False,
                "isInLoop": False,
                "sourceType": source_type,
                "targetType": target_type,
            },
        }

    def _render_node(self, node: ResolvedNode, position_index: int) -> dict[str, Any]:
        atom = self._atoms_by_id.get(node.asset_id)
        pos = self._node_position(position_index)

        data = self._render_data_from_atom_template(atom, node)
        if data is None:
            data = self._render_data_builtin(atom, node)

        data["_factory"] = {
            "asset_id": node.asset_id,
            "asset_version": node.asset_version,
            "selection_reason": node.selection_reason,
            "confidence": node.confidence,
            "inputs": node.inputs,
            "params": node.params,
        }

        return {
            "id": node.id,
            "type": "custom",
            "position": pos,
            "positionAbsolute": dict(pos),
            "width": _NODE_W,
            "height": _NODE_H,
            "sourcePosition": "right",
            "targetPosition": "left",
            "selected": False,
            "data": data,
        }

    def _render_data_from_atom_template(
        self, atom: AtomDef | None, node: ResolvedNode
    ) -> dict[str, Any] | None:
        """Try to render the atom's Dify projection template (Jinja2 -> YAML).

        Returns None when the atom is unknown, has no Dify projection, the
        projection is marked not_supported, or the template is a placeholder
        / unparseable. Caller falls back to built-in rendering.
        """
        if atom is None:
            return None
        proj = atom.projections.get("dify")
        if proj is None or proj.not_supported or not proj.template:
            return None
        tmpl_src = proj.template.strip()
        # Skip legacy placeholder templates that pre-date Phase 8 Day 2.
        if not tmpl_src or tmpl_src.startswith("# Phase"):
            return None
        try:
            env = jinja2.Environment(
                undefined=jinja2.StrictUndefined,
                trim_blocks=False,
                lstrip_blocks=False,
                autoescape=False,
            )
            tmpl = env.from_string(proj.template)
            rendered = tmpl.render(atom=atom, node=node, helpers=_HELPERS)
            data = yaml.safe_load(rendered)
        except (jinja2.TemplateError, yaml.YAMLError) as exc:
            logger.warning(
                "atom %s dify template render failed (%s); falling back to builtin",
                atom.asset_id,
                exc,
            )
            emit(
                "L3",
                "DifyCompiler",
                "atom_template_fallback",
                f"asset_id={atom.asset_id} reason={type(exc).__name__}",
            )
            return None
        if not isinstance(data, dict):
            logger.warning(
                "atom %s dify template did not yield a mapping; got %s",
                atom.asset_id,
                type(data).__name__,
            )
            return None
        emit(
            "L3",
            "DifyCompiler",
            "atom_template_used",
            f"asset_id={atom.asset_id} keys={sorted(data.keys())}",
        )
        return data

    def _render_data_builtin(
        self, atom: AtomDef | None, node: ResolvedNode
    ) -> dict[str, Any]:
        sub = atom.subcategory if atom else "Unknown"
        data_type = _DATA_TYPE_MAP.get(sub, "code")
        title = atom.name if atom else node.asset_id

        data: dict[str, Any] = {
            "type": data_type,
            "title": title,
            "desc": node.selection_reason[:200],
            "selected": False,
        }
        if data_type == "llm":
            data.update(self._llm_data(node))
        elif data_type == "http-request":
            data.update(self._http_data(node))
        else:
            data.update(self._code_data(node))
        return data

    def _llm_data(self, node: ResolvedNode) -> dict[str, Any]:
        task_key = (node.llm_task_type or "").replace(" ", "_").replace("/", "")
        size = node.llm_size or ""
        model_placeholder = f"${{RUIDONG_MODEL_FOR_{task_key}_{size}}}"

        prompts: list[dict[str, Any]] = [
            {
                "id": str(uuid.uuid4()),
                "role": "system",
                "text": (
                    f"prompt_id={node.prompt_id} (resolved at runtime via prompt registry)"
                    if node.prompt_id
                    else ""
                ),
            }
        ]
        for in_var in node.inputs.keys():
            prompts.append({"role": "user", "text": f"{{{{#{in_var}#}}}}"})

        return {
            "model": {
                "provider": "langgenius/openai_api_compatible/openai_api_compatible",
                "name": model_placeholder,
                "mode": "chat",
                "completion_params": {"temperature": 0.7},
            },
            "prompt_template": prompts,
            "context": {"enabled": False, "variable_selector": []},
            "vision": {"enabled": False},
            "variables": [],
            "_llm_routing": {
                "task_type": node.llm_task_type,
                "size": node.llm_size,
                "fallback_chain": node.llm_fallback_chain,
                "prompt_id": node.prompt_id,
            },
        }

    def _http_data(self, node: ResolvedNode) -> dict[str, Any]:
        return {
            "method": node.params.get("method", "POST"),
            "url": node.params.get("url", "${PLACEHOLDER_URL}"),
            "headers": node.params.get("headers", ""),
            "params": "",
            "body": {"type": "json", "data": []},
            "timeout": {"max_connect_timeout": 0, "max_read_timeout": 0, "max_write_timeout": 0},
            "authorization": {"type": "no-auth", "config": None},
            "variables": [],
        }

    def _code_data(self, node: ResolvedNode) -> dict[str, Any]:
        return {
            "code_language": "python3",
            "code": (
                f"# placeholder for atom={node.asset_id} (subcategory binding incomplete)\n"
                f"def main(**kwargs):\n"
                f"    return {{'result': str(kwargs)}}\n"
            ),
            "variables": [
                {"variable": k, "value_selector": []} for k in node.inputs.keys()
            ],
            "outputs": {"result": {"type": "string"}},
        }
