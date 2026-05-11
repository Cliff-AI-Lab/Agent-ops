"""HandoffRoutingInjector - Phase 10 W5 D1 branch-based handoff routing.

Sibling to W3 D3 HandoffWebhookInjector. The chain injector executes
every downstream webhook sequentially, which is wrong for triage:
triage's job is to PICK ONE specialist, not call all of them.

This module mutates the source agent's Dify YAML to:

  1. find the final LLM node (the classifier)
  2. attach an if-else node consuming llm_node.text
  3. for each outgoing handoff edge, create one case in the if-else
     (condition: llm.text contains target_agent id)
  4. each case connects to one http-request webhook node
  5. all webhooks + an else branch converge at node_end

Compared to W3 D3:

  before (chain):   LLM -> webhook_A -> webhook_B -> webhook_C -> End
  after (routing):  LLM -> if-else -> case_A -> webhook_A --+
                                  -> case_B -> webhook_B --+--> End
                                  -> case_C -> webhook_C --+
                                  -> else   -> (none) -----+

Scope (W5 D1 MVP):
  - condition uses `contains` operator on the LLM text (looser than
    structured JSON parsing, but works without forcing prompt format
    discipline); W5 D2 will switch to JSON extract + equals
  - else branch drops straight to End (no fallback specialist; future
    W5 D3 can wire a fallback handoff edge into the else)
  - one if-else node max per agent; W5 D4 may chain ifs for hierarchical
    handoffs

R3 reuse audit:
  - Reuses HandoffEdgeMetadata (W3 D2)
  - Reuses the synthetic node_start / node_end IDs
  - Reuses the same http-request webhook node shape as
    HandoffWebhookInjector (so deploy-keys + system-diff still work)
  - No schema migration
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

import yaml

from app.delivery.multi_agent_dify_projector import HandoffEdgeMetadata


ConditionMode = Literal["contains", "is"]
"""contains: loose (default; LLM may chat around the answer)
is:       strict (LLM must output ONLY the specialist id; we also append
           a prompt constraint so the LLM knows the contract)
"""


logger = logging.getLogger(__name__)


SYNTHETIC_END_ID = "node_end"
SYNTHETIC_START_ID = "node_start"
_IFELSE_X = 800
_IFELSE_Y = 245
_BRANCH_X_BASE = 1100
_BRANCH_Y_BASE = 100
_BRANCH_Y_STEP = 180
_NODE_W = 244
_NODE_H = 90


@dataclass
class InjectedRoute:
    """One injected branch (case + webhook)."""

    target_agent: str
    target_dify_app_id: str | None
    case_id: str
    webhook_node_id: str
    url: str


@dataclass
class RoutingInjectionResult:
    """Outcome of inject_routing() for one agent."""

    source_agent: str
    ifelse_node_id: str = ""
    yaml_chars_in: int = 0
    yaml_chars_out: int = 0
    routes: list[InjectedRoute] = field(default_factory=list)
    skipped_reason: str = ""
    yaml_out: str = ""

    @property
    def did_inject(self) -> bool:
        return bool(self.routes) and not self.skipped_reason


class HandoffRoutingInjector:
    """Stateless. Construct once per pipeline run.

    Args:
        dify_base_url: base URL for the http-request webhook nodes
                       (default: http://localhost:8080).
        llm_var_selector: the (node_id, "text") tuple that the if-else
                         reads. Defaults to a magic placeholder the
                         caller is expected to remap after injection.
                         When inject_routing() picks the actual LLM node
                         dynamically (the latest LLM in the graph) this
                         is overridden automatically.
    """

    DEFAULT_BASE_URL = "http://localhost:8080"

    def __init__(self, dify_base_url: str | None = None):
        self.dify_base_url = (dify_base_url or self.DEFAULT_BASE_URL).rstrip("/")

    def inject_routing(
        self,
        source_agent: str,
        source_yaml: str,
        edges_for_source: list[HandoffEdgeMetadata],
        *,
        condition_mode: ConditionMode = "contains",
        fallback_agent: str | None = None,
    ) -> RoutingInjectionResult:
        in_chars = len(source_yaml)
        result = RoutingInjectionResult(
            source_agent=source_agent,
            yaml_chars_in=in_chars,
            yaml_chars_out=in_chars,
            yaml_out=source_yaml,
        )

        outgoing = [e for e in edges_for_source if e.from_agent == source_agent]
        if not outgoing:
            result.skipped_reason = "no outgoing handoff edges"
            return result

        try:
            data = yaml.safe_load(source_yaml)
        except yaml.YAMLError as exc:
            result.skipped_reason = f"yaml parse error: {exc}"
            return result
        if not isinstance(data, dict):
            result.skipped_reason = "yaml top-level is not a mapping"
            return result
        try:
            graph = data["workflow"]["graph"]
            nodes = graph["nodes"]
            edges = graph["edges"]
        except (KeyError, TypeError):
            result.skipped_reason = "workflow.graph.nodes/edges missing"
            return result
        if not isinstance(nodes, list) or not isinstance(edges, list):
            result.skipped_reason = "graph.nodes or edges not a list"
            return result

        # Find the latest LLM node in the chain (the classifier output)
        llm_id = self._find_last_llm(nodes)
        if llm_id is None:
            result.skipped_reason = "no llm node to drive routing"
            return result

        # Find node_end
        if not any(isinstance(n, dict) and n.get("id") == SYNTHETIC_END_ID
                   for n in nodes):
            result.skipped_reason = f"missing synthetic {SYNTHETIC_END_ID}"
            return result

        # Drop edges currently feeding End (we will re-route through if-else)
        edges = [e for e in edges
                 if not (isinstance(e, dict) and e.get("target") == SYNTHETIC_END_ID)]
        # Drop edges currently going from LLM to End (or any next-of-LLM)
        edges = [e for e in edges
                 if not (isinstance(e, dict) and e.get("source") == llm_id)]

        # In strict mode, append a prompt constraint to the LLM node telling it
        # to output ONLY one of the candidate specialist ids. This sets up the
        # equals comparison below to work.
        if condition_mode == "is":
            self._tighten_llm_prompt(
                nodes=nodes,
                llm_id=llm_id,
                candidates=[e.to_agent for e in outgoing],
            )

        # Validate fallback_agent if specified
        outgoing_targets = {e.to_agent for e in outgoing}
        if fallback_agent is not None and fallback_agent not in outgoing_targets:
            result.skipped_reason = (
                f"fallback_agent={fallback_agent!r} not in outgoing handoffs "
                f"{sorted(outgoing_targets)}"
            )
            return result

        # Build if-else node
        ifelse_id = f"ifelse_{source_agent}_{uuid.uuid4().hex[:6]}"
        cases: list[dict[str, Any]] = []
        routes: list[InjectedRoute] = []
        operator = "is" if condition_mode == "is" else "contains"
        fallback_webhook_id: str | None = None
        for i, edge in enumerate(outgoing):
            case_id = f"case_{edge.to_agent}_{uuid.uuid4().hex[:4]}"
            cases.append({
                "case_id": case_id,
                "logical_operator": "and",
                "conditions": [
                    {
                        "id": uuid.uuid4().hex,
                        "varType": "string",
                        "variable_selector": [llm_id, "text"],
                        "comparison_operator": operator,
                        "value": edge.to_agent,
                    },
                ],
            })
            # Build webhook node for this branch
            wh_id = f"handoff_{source_agent}_to_{edge.to_agent}_{uuid.uuid4().hex[:6]}"
            target_app_id = edge.to_dify_app_id or ""
            url = f"{self.dify_base_url}/v1/workflows/run"
            env_key = (
                "DIFY_APP_API_KEY_"
                + edge.to_agent.upper().replace("-", "_").replace(".", "_")
            )
            nodes.append({
                "id": wh_id,
                "type": "custom",
                "position": {
                    "x": _BRANCH_X_BASE,
                    "y": _BRANCH_Y_BASE + i * _BRANCH_Y_STEP,
                },
                "positionAbsolute": {
                    "x": _BRANCH_X_BASE,
                    "y": _BRANCH_Y_BASE + i * _BRANCH_Y_STEP,
                },
                "width": _NODE_W, "height": _NODE_H,
                "sourcePosition": "right", "targetPosition": "left",
                "selected": False,
                "data": {
                    "type": "http-request",
                    "title": f"handoff to {edge.to_agent}",
                    "desc": (
                        f"factory-injected routing webhook (case={case_id}) "
                        f"to specialist {edge.to_agent}; when: {edge.when or '-'}"
                    )[:240],
                    "selected": False,
                    "method": "POST",
                    "url": url,
                    "headers": (
                        "Content-Type: application/json\n"
                        f"Authorization: Bearer ${{{env_key}}}"
                    ),
                    "params": "",
                    "body": {
                        "type": "json",
                        "data": [
                            {"key": "inputs", "type": "object", "value": "{}"},
                            {"key": "response_mode", "type": "text", "value": "blocking"},
                            {"key": "user", "type": "text", "value": "factory-handoff"},
                        ],
                    },
                    "timeout": {
                        "max_connect_timeout": 5,
                        "max_read_timeout": 60,
                        "max_write_timeout": 5,
                    },
                    "authorization": {"type": "no-auth", "config": None},
                    "variables": [],
                    "_factory_handoff": {
                        "target_agent": edge.to_agent,
                        "target_dify_app_id": target_app_id or None,
                        "case_id": case_id,
                        "when": edge.when,
                        "auth_env_key": env_key,
                        "routing_mode": "switch",
                    },
                },
            })
            # if-else -> webhook edge (sourceHandle=case_id)
            edges.append({
                "id": f"{ifelse_id}-{case_id}-{wh_id}",
                "source": ifelse_id,
                "sourceHandle": case_id,
                "target": wh_id,
                "targetHandle": "target",
                "type": "custom",
                "zIndex": 0,
                "data": {
                    "isInIteration": False, "isInLoop": False,
                    "sourceType": "if-else", "targetType": "http-request",
                },
            })
            # webhook -> End
            edges.append({
                "id": f"{wh_id}-end",
                "source": wh_id, "sourceHandle": "source",
                "target": SYNTHETIC_END_ID, "targetHandle": "target",
                "type": "custom", "zIndex": 0,
                "data": {
                    "isInIteration": False, "isInLoop": False,
                    "sourceType": "http-request", "targetType": "end",
                },
            })
            routes.append(InjectedRoute(
                target_agent=edge.to_agent,
                target_dify_app_id=edge.to_dify_app_id,
                case_id=case_id,
                webhook_node_id=wh_id,
                url=url,
            ))
            if fallback_agent and edge.to_agent == fallback_agent:
                fallback_webhook_id = wh_id

        # Build the if-else node itself; record the mode + operator so
        # system-diff / inspectors can see what wiring was used.
        nodes.append({
            "id": ifelse_id,
            "type": "custom",
            "position": {"x": _IFELSE_X, "y": _IFELSE_Y},
            "positionAbsolute": {"x": _IFELSE_X, "y": _IFELSE_Y},
            "width": _NODE_W, "height": _NODE_H,
            "sourcePosition": "right", "targetPosition": "left",
            "selected": False,
            "data": {
                "type": "if-else",
                "title": f"handoff routing ({len(outgoing)} cases)",
                "desc": (
                    f"factory-injected routing on llm output for {source_agent}"
                )[:240],
                "selected": False,
                "logical_operator": "and",
                "cases": cases,
                "isInIteration": False,
                "isInLoop": False,
                "_factory_routing": {
                    "source_agent": source_agent,
                    "llm_node_id": llm_id,
                    "case_count": len(cases),
                    "condition_mode": condition_mode,
                    "operator": operator,
                    "fallback_agent": fallback_agent,
                    "fallback_webhook_id": fallback_webhook_id,
                },
            },
        })

        # LLM -> if-else
        edges.append({
            "id": f"{llm_id}-ifelse-{ifelse_id}",
            "source": llm_id, "sourceHandle": "source",
            "target": ifelse_id, "targetHandle": "target",
            "type": "custom", "zIndex": 0,
            "data": {
                "isInIteration": False, "isInLoop": False,
                "sourceType": "llm", "targetType": "if-else",
            },
        })

        # if-else else (default) -> fallback webhook if configured else End
        else_target = fallback_webhook_id or SYNTHETIC_END_ID
        else_target_type = "http-request" if fallback_webhook_id else "end"
        edges.append({
            "id": f"{ifelse_id}-else-{else_target}",
            "source": ifelse_id, "sourceHandle": "false",
            "target": else_target, "targetHandle": "target",
            "type": "custom", "zIndex": 0,
            "data": {
                "isInIteration": False, "isInLoop": False,
                "sourceType": "if-else", "targetType": else_target_type,
            },
        })

        graph["nodes"] = nodes
        graph["edges"] = edges
        out_yaml = yaml.safe_dump(
            data, allow_unicode=True, sort_keys=False, default_flow_style=False
        )
        result.yaml_out = out_yaml
        result.yaml_chars_out = len(out_yaml)
        result.ifelse_node_id = ifelse_id
        result.routes = routes
        return result

    # ----- internals ----------------------------------------------------

    @staticmethod
    def _tighten_llm_prompt(
        nodes: list, llm_id: str, candidates: list[str],
    ) -> None:
        """Append a strict routing constraint to the LLM node's prompt_template.

        The constraint tells the LLM the EXACT closed set of specialist ids it
        is allowed to output, with no surrounding text. The matching if-else
        case will then use `is` (equals) instead of `contains`, eliminating
        ambiguity (e.g. an LLM saying "I think booking is fine" would have
        matched `booking` AND `book` cases under `contains`).
        """
        if not candidates:
            return
        target_node = next(
            (n for n in nodes if isinstance(n, dict) and n.get("id") == llm_id),
            None,
        )
        if target_node is None:
            return
        data = target_node.setdefault("data", {})
        prompt_template = data.setdefault("prompt_template", [])
        if not isinstance(prompt_template, list):
            return
        constraint_text = (
            "FACTORY ROUTING CONSTRAINT (auto-injected by HandoffRoutingInjector):\n"
            "Your entire response MUST be exactly one of the following "
            f"specialist ids, with no prefix, suffix, quotes, or extra "
            f"whitespace: {', '.join(candidates)}\n"
            "If the user's request fits none of these, output the literal "
            "string `__none__` (the workflow will fall through to the else "
            "branch)."
        )
        # Add as a final system message; safe to append regardless of role mix
        prompt_template.append({
            "id": uuid.uuid4().hex,
            "role": "system",
            "text": constraint_text,
        })
        # mark on the node so verifier / system-diff can see it
        data.setdefault("_factory_routing_prompt", {})
        data["_factory_routing_prompt"] = {
            "appended": True,
            "candidates": list(candidates),
        }

    @staticmethod
    def _find_last_llm(nodes: list) -> str | None:
        for n in reversed(nodes):
            if not isinstance(n, dict):
                continue
            data = n.get("data") or {}
            if data.get("type") == "llm":
                nid = n.get("id")
                if isinstance(nid, str):
                    return nid
        return None
