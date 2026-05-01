"""Bind submodule: build ResolvedNode + edges with type_check.

Edge type_check uses each atom's `outputs.properties.output.x-data-type`
when available. Mismatch -> needs_adapter=True (Resolver caller logs the gap;
auto-insertion of `combo.type_adapter.X_to_Y.v1` is Phase 2).
"""

from __future__ import annotations

import re

from app.core.pipelines.factory.ir import (
    ResolvedEdge,
    ResolvedNode,
    StepSpec,
    StructuredIntent,
    TypeCheck,
)
from app.registry.atom_loader import AtomDef


_INPUT_REF_RE = re.compile(r"^\$([a-zA-Z0-9_]+)\.([a-zA-Z0-9_]+)$")


def build_node(
    step: StepSpec,
    atom: AtomDef,
    confidence: float,
    reason: str,
    llm_task_type: str | None = None,
    llm_size: str | None = None,
    prompt_id: str | None = None,
) -> ResolvedNode:
    """Construct a ResolvedNode from a step + selected atom."""
    fallback_chain: list[str] = []
    if atom.subcategory == "LLM" and llm_size:
        if atom.model_routing and atom.model_routing.default_fallback_chain:
            fallback_chain = list(
                atom.model_routing.default_fallback_chain.get(llm_size, [])
            )
        if not fallback_chain:
            fallback_chain = [llm_size]

    return ResolvedNode(
        id=step.id,
        asset_id=atom.asset_id,
        asset_version=atom.version,
        confidence=confidence,
        selection_reason=reason,
        inputs=dict(step.inputs),
        params=dict(step.constraints),
        llm_task_type=llm_task_type,
        llm_size=llm_size,
        llm_fallback_chain=fallback_chain,
        prompt_id=prompt_id,
    )


def build_edges(
    intent: StructuredIntent,
    nodes_by_id: dict[str, ResolvedNode],
    atoms_by_id: dict[str, AtomDef],
) -> list[ResolvedEdge]:
    """Walk step.inputs `$sX.output` references and emit ResolvedEdge with type_check."""
    edges: list[ResolvedEdge] = []
    for step in intent.steps:
        for var_name, ref in step.inputs.items():
            if not isinstance(ref, str):
                continue
            m = _INPUT_REF_RE.match(ref)
            if not m:
                continue
            from_id, from_var = m.group(1), m.group(2)
            if from_id not in nodes_by_id:
                continue
            from_atom = atoms_by_id.get(nodes_by_id[from_id].asset_id)
            to_atom = atoms_by_id.get(nodes_by_id[step.id].asset_id)
            type_check = _build_type_check(from_atom, from_var, to_atom, var_name)
            needs_adapter = (
                type_check.from_type != type_check.to_type
                and type_check.from_type != "unknown"
                and type_check.to_type != "unknown"
            )
            edges.append(
                ResolvedEdge(
                    from_node=from_id,
                    to_node=step.id,
                    from_var=from_var,
                    to_var=var_name,
                    type_check=type_check,
                    needs_adapter=needs_adapter,
                    adapter_combo=(
                        f"combo.type_adapter.{type_check.from_type}_to_{type_check.to_type}.v1"
                        if needs_adapter
                        else None
                    ),
                )
            )
    return edges


def _build_type_check(
    from_atom: AtomDef | None,
    from_var: str,
    to_atom: AtomDef | None,
    to_var: str,
) -> TypeCheck:
    return TypeCheck(
        from_type=_extract_x_data_type(from_atom, "outputs", from_var),
        to_type=_extract_x_data_type(to_atom, "inputs", to_var),
    )


def _extract_x_data_type(atom: AtomDef | None, side: str, var: str) -> str:
    if atom is None:
        return "unknown"
    schema = atom.io_schema.inputs if side == "inputs" else atom.io_schema.outputs
    props = schema.get("properties") or {}
    field = props.get(var) or {}
    if isinstance(field, dict):
        x_type = field.get("x-data-type")
        if x_type:
            return str(x_type)
    return "unknown"


def select_target(
    intent: StructuredIntent,
    nodes_by_id: dict[str, ResolvedNode],
    atoms_by_id: dict[str, AtomDef],
) -> tuple[str, dict[str, list[str]] | None]:
    """Decide dify / n8n / hybrid from atom subcategory composition.

    Heuristics (Phase 1 simple):
      - All atoms support dify only -> 'dify'
      - All atoms support n8n only -> 'n8n'
      - Trigger nodes (Schedule subcategory) + integration nodes (DB/HTTP/Notify)
        + LLM nodes -> 'hybrid' (n8n drives, Dify handles AI)
    """
    n8n_subs = {"DB", "HTTP", "Notify", "Schedule", "WebSearch"}
    dify_subs = {"LLM"}

    n8n_nodes = []
    dify_nodes = []
    for node_id, node in nodes_by_id.items():
        atom = atoms_by_id.get(node.asset_id)
        if atom is None:
            continue
        if atom.subcategory in dify_subs:
            dify_nodes.append(node_id)
        elif atom.subcategory in n8n_subs:
            n8n_nodes.append(node_id)
        else:
            n8n_nodes.append(node_id)

    if dify_nodes and n8n_nodes:
        return "hybrid", {"n8n_nodes": n8n_nodes, "dify_nodes": dify_nodes}
    if dify_nodes:
        return "dify", None
    return "n8n", None
