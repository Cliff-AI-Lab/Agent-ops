"""N8nCompilerImpl V1 - emits n8n workflow JSON from ResolvedDAG.

Per [[Phase-5-n8n与多模式]]: handles target='n8n' or 'hybrid' (n8n-scoped nodes).
Output: n8n workflow JSON string suitable for `n8n import:workflow`.

n8n workflow JSON format (n8n >= 1.0):
{
  "name": "specimen_<id>",
  "active": false,
  "nodes": [
    {
      "id": "uuid",
      "name": "Display Name",
      "type": "n8n-nodes-base.<type>",
      "typeVersion": 1,
      "position": [x, y],
      "parameters": {...},
      "credentials": {...}
    }
  ],
  "connections": {
    "Source Node Name": {
      "main": [[{ "node": "Target Node Name", "type": "main", "index": 0 }]]
    }
  },
  "settings": { "executionOrder": "v1" }
}

Phase 5 W11 will refine atom.projections.n8n templates to per-atom Jinja2.
For V1 we use a built-in subcategory -> n8n-nodes-base type map.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from app.core.pipelines.factory.ir import ResolvedDAG, ResolvedNode
from app.core.trace.bus import emit
from app.registry.atom_loader import AtomDef


# subcategory -> n8n node type
_N8N_NODE_TYPE: dict[str, str] = {
    "Schedule": "n8n-nodes-base.scheduleTrigger",
    "DB": "n8n-nodes-base.postgres",
    "HTTP": "n8n-nodes-base.httpRequest",
    "LLM": "@n8n/n8n-nodes-langchain.openAi",
    "Notify": "n8n-nodes-base.httpRequest",
    "OCR": "n8n-nodes-base.httpRequest",  # via adapter
    "TTS": "n8n-nodes-base.httpRequest",
    "ASR": "n8n-nodes-base.httpRequest",
    "Embedding": "n8n-nodes-base.httpRequest",
    "VectorDB": "n8n-nodes-base.httpRequest",
    "Chart": "n8n-nodes-base.code",
    "WebSearch": "n8n-nodes-base.httpRequest",
    "DocParser": "n8n-nodes-base.httpRequest",
}


class N8nCompilerImpl:
    """V1 n8n compiler. Same Protocol as DSLCompiler but emits JSON not YAML."""

    def __init__(self, atom_loader: Any | None = None) -> None:
        self._atoms_by_id: dict[str, AtomDef] = {}
        if atom_loader is not None:
            self._loader = atom_loader

    def index(self, atoms: dict[str, AtomDef]) -> None:
        self._atoms_by_id = atoms

    def compile(self, dag: ResolvedDAG) -> str:
        if dag.target not in ("n8n", "hybrid"):
            raise ValueError(
                f"N8nCompilerImpl only handles n8n/hybrid targets, got {dag.target}"
            )

        emit(
            "L3",
            "N8nCompiler",
            "compile_start",
            f"target={dag.target} nodes={len(dag.nodes)}",
        )

        scoped_nodes = self._scope_nodes(dag)
        scoped_edges = self._scope_edges(dag, scoped_nodes)
        rendered_nodes = [self._render_node(n, idx) for idx, n in enumerate(scoped_nodes)]
        connections = self._render_connections(rendered_nodes, scoped_edges)

        workflow: dict[str, Any] = {
            "name": f"specimen_{dag.intent_ref[:30].replace(' ', '_')}",
            "active": False,
            "nodes": rendered_nodes,
            "connections": connections,
            "settings": {"executionOrder": "v1"},
            "factory_metadata": {
                "intent_ref": dag.intent_ref,
                "pattern_id": dag.pattern_id,
                "target": dag.target,
                "issues": dag.issues,
                "scope": "n8n_nodes" if dag.target == "hybrid" else "all",
            },
        }
        out = json.dumps(workflow, ensure_ascii=False, indent=2)
        emit(
            "L3",
            "N8nCompiler",
            "compile_done",
            f"json_chars={len(out)} nodes={len(rendered_nodes)}",
        )
        return out

    def _scope_nodes(self, dag: ResolvedDAG) -> list[ResolvedNode]:
        if dag.target == "n8n":
            return list(dag.nodes)
        if dag.target_split:
            ids = set(dag.target_split.get("n8n_nodes", []))
            return [n for n in dag.nodes if n.id in ids]
        return list(dag.nodes)

    def _scope_edges(self, dag, scoped_nodes):
        ids = {n.id for n in scoped_nodes}
        return [e for e in dag.edges if e.from_node in ids and e.to_node in ids]

    def _render_node(self, node: ResolvedNode, idx: int) -> dict[str, Any]:
        atom = self._atoms_by_id.get(node.asset_id)
        subcategory = atom.subcategory if atom else "Unknown"
        node_type = _N8N_NODE_TYPE.get(subcategory, "n8n-nodes-base.httpRequest")

        # n8n nodes have a `name` (display) and `id` (uuid). We use atom.name for
        # display (English fallback) and node.id as a stable identifier in the
        # connections graph.
        name = (atom.name_en or atom.name) if atom else node.asset_id
        if not name:
            name = node.asset_id

        # Position layout: simple left-to-right grid
        x = 240 + idx * 220
        y = 300

        parameters: dict[str, Any] = {}
        if subcategory == "Schedule":
            parameters = {"rule": {"interval": [{"field": "weeks"}]}}
            cron_expr = node.params.get("cron_expr")
            if cron_expr:
                parameters = {
                    "triggerTimes": {"item": [{"mode": "everyWeek", "cronExpression": cron_expr}]}
                }
        elif subcategory == "DB":
            parameters = {
                "operation": "executeQuery",
                "query": node.inputs.get("sql", ""),
            }
        elif subcategory == "HTTP" or subcategory == "Notify":
            parameters = {
                "url": node.inputs.get("url") or node.inputs.get("webhook_url", ""),
                "method": node.inputs.get("method", "POST"),
                "sendBody": True,
                "bodyParametersUi": {
                    "parameter": [
                        {"name": k, "value": str(v)}
                        for k, v in (node.inputs.get("body") or {}).items()
                    ]
                    if isinstance(node.inputs.get("body"), dict)
                    else []
                },
            }
        elif subcategory == "LLM":
            parameters = {
                "operation": "message",
                "modelId": {
                    "__rl": True,
                    "value": "${RUIDONG_MODEL_FOR_"
                    + (node.llm_task_type or "GENERIC").replace(" ", "_").replace("/", "")
                    + "_"
                    + (node.llm_size or "M")
                    + "}",
                    "mode": "id",
                },
                "messages": {
                    "values": [
                        {"role": "system", "content": f"prompt_id={node.prompt_id or ''}"}
                    ]
                },
            }
        else:
            # Generic HTTP fallback
            parameters = {"url": "", "method": "GET"}

        rendered: dict[str, Any] = {
            "id": uuid.uuid5(uuid.NAMESPACE_OID, node.id).hex[:16],
            "name": f"{name} ({node.id})",
            "type": node_type,
            "typeVersion": 1,
            "position": [x, y],
            "parameters": parameters,
            "factory_data": {
                "asset_id": node.asset_id,
                "asset_version": node.asset_version,
                "selection_reason": node.selection_reason,
                "confidence": node.confidence,
            },
        }
        return rendered

    def _render_connections(
        self, rendered_nodes: list[dict[str, Any]], edges
    ) -> dict[str, Any]:
        # Build node_id -> rendered_name lookup
        # Note: rendered nodes' factory_data has no node.id, so we re-thread via the
        # ResolvedNode.id we stored as part of the display name "(s1)".
        name_by_node_id: dict[str, str] = {}
        for rn in rendered_nodes:
            display = rn["name"]
            # Extract trailing "(node_id)"
            if "(" in display and display.endswith(")"):
                node_id = display.rsplit("(", 1)[1].rstrip(")")
                name_by_node_id[node_id] = display

        connections: dict[str, Any] = {}
        for edge in edges:
            src_name = name_by_node_id.get(edge.from_node)
            tgt_name = name_by_node_id.get(edge.to_node)
            if src_name is None or tgt_name is None:
                continue
            entry = connections.setdefault(src_name, {"main": [[]]})
            entry["main"][0].append(
                {"node": tgt_name, "type": "main", "index": 0}
            )
        return connections
