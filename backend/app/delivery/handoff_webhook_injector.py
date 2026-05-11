"""HandoffWebhookInjector - Phase 10 W3 D3 inject downstream webhook nodes.

After W3 D1 published each specialist as its own Dify app and W3 D2
recorded every handoff edge with its from/to dify_app_id, this module
modifies the source agent's Dify YAML to actually CALL the downstream
agents at runtime via HTTP-request nodes.

Scope (W3 D3 MVP):
  - one webhook node per outgoing handoff edge
  - inserted between the source agent's last business node and the End
    node so the handoff is the agent's final action
  - url targets <DIFY_BASE>/v1/workflows/run pointing at the downstream
    app_id (Dify service API)
  - authentication via Bearer ${DIFY_APP_API_KEY_<TARGET_AGENT_ID>}
    environment placeholder; real keys are populated by W3 D4 deploy
    helper, never inlined in YAML

Non-goals (W3 D4 / W4):
  - response-driven branching (which specialist to actually pick)
  - conditional handoff (LLM-judged "when" expression)
  - retries / backoff
  - real Dify Service API key provisioning

R3 reuse audit:
  - Reuses ResolvedDAG model (read-only)
  - Reuses DifyCompiler-emitted YAML shape (node.data._factory.asset_id,
    synthetic node_start / node_end IDs)
  - No schema migration
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

import yaml

from app.delivery.multi_agent_dify_projector import HandoffEdgeMetadata


logger = logging.getLogger(__name__)


SYNTHETIC_END_ID = "node_end"
SYNTHETIC_START_ID = "node_start"
# Layout: drop webhooks slightly below the workflow row so React Flow
# doesn't overlap them with the main chain.
_WEBHOOK_BASE_Y = 420
_WEBHOOK_DX = 304
_WEBHOOK_NODE_W = 244
_WEBHOOK_NODE_H = 90


@dataclass
class InjectedWebhook:
    """One injected webhook node + the edge it serves."""

    target_agent: str
    target_dify_app_id: str | None
    node_id: str
    url: str


@dataclass
class InjectionResult:
    """Outcome of inject() for one agent."""

    source_agent: str
    yaml_chars_in: int
    yaml_chars_out: int
    injected: list[InjectedWebhook] = field(default_factory=list)
    skipped_reason: str = ""
    yaml_out: str = ""

    @property
    def did_inject(self) -> bool:
        return bool(self.injected)


class HandoffWebhookInjector:
    """Stateless. Construct once per pipeline run.

    Args:
        dify_base_url: base URL used in the HTTP-request node's url field.
                       e.g. http://localhost:8080 (the Dify service API
                       lives at $base/v1/workflows/run).
    """

    DEFAULT_BASE_URL = "http://localhost:8080"

    def __init__(self, dify_base_url: str | None = None):
        self.dify_base_url = (dify_base_url or self.DEFAULT_BASE_URL).rstrip("/")

    def inject(
        self,
        source_agent: str,
        source_yaml: str,
        edges_for_source: list[HandoffEdgeMetadata],
    ) -> InjectionResult:
        """Inject downstream webhook nodes into the source agent's YAML.

        Returns InjectionResult with the modified YAML in yaml_out. If the
        YAML can't be parsed or has no End node, returns the original yaml
        in yaml_out and records skipped_reason.
        """
        in_chars = len(source_yaml)
        result = InjectionResult(
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

        # Find the End node (target of injection)
        end_node = next(
            (n for n in nodes if isinstance(n, dict) and n.get("id") == SYNTHETIC_END_ID),
            None,
        )
        if end_node is None:
            result.skipped_reason = f"missing synthetic {SYNTHETIC_END_ID} node"
            return result

        # Find the edge currently feeding End (the last business node -> End)
        # so we can re-route through the new webhook chain.
        edges_to_end = [
            e for e in edges
            if isinstance(e, dict) and e.get("target") == SYNTHETIC_END_ID
        ]
        if not edges_to_end:
            result.skipped_reason = "no edge feeds the End node"
            return result

        # Insert webhook nodes between the last edge-source and End.
        # For simplicity we hook off the FIRST edge_to_end; multiple-fan-in
        # is rare for factory output and lands in W4.
        anchor_edge = edges_to_end[0]
        prev_source = anchor_edge["source"]

        # Remove the original edge prev -> End; we will rebuild the chain.
        edges = [e for e in edges if not (
            isinstance(e, dict)
            and e.get("source") == prev_source
            and e.get("target") == SYNTHETIC_END_ID
        )]

        last_id_in_chain = prev_source
        last_type_in_chain = self._data_type_of(nodes, prev_source) or "code"

        for i, edge in enumerate(outgoing):
            node_id = f"handoff_{source_agent}_to_{edge.to_agent}_{uuid.uuid4().hex[:8]}"
            target_app_id = edge.to_dify_app_id or ""
            url = self._service_api_url(target_app_id)
            wh_node = self._build_webhook_node(
                node_id=node_id,
                target_agent=edge.to_agent,
                target_app_id=target_app_id,
                url=url,
                position_index=i + 1,
                when=edge.when,
            )
            nodes.append(wh_node)

            # Connect last_id_in_chain -> wh_node
            edges.append(self._build_edge(
                last_id_in_chain, node_id,
                last_type_in_chain, "http-request",
            ))

            result.injected.append(InjectedWebhook(
                target_agent=edge.to_agent,
                target_dify_app_id=edge.to_dify_app_id,
                node_id=node_id,
                url=url,
            ))

            last_id_in_chain = node_id
            last_type_in_chain = "http-request"

        # Reconnect last webhook -> End so workflow remains valid
        edges.append(self._build_edge(
            last_id_in_chain, SYNTHETIC_END_ID,
            last_type_in_chain, "end",
        ))

        graph["nodes"] = nodes
        graph["edges"] = edges
        out_yaml = yaml.safe_dump(
            data, allow_unicode=True, sort_keys=False, default_flow_style=False
        )
        result.yaml_out = out_yaml
        result.yaml_chars_out = len(out_yaml)
        return result

    # ----- internals -----------------------------------------------------

    def _service_api_url(self, target_app_id: str) -> str:
        if not target_app_id:
            return f"{self.dify_base_url}/v1/workflows/run"
        return f"{self.dify_base_url}/v1/workflows/run"

    @staticmethod
    def _data_type_of(nodes: list, node_id: str) -> str | None:
        for n in nodes:
            if isinstance(n, dict) and n.get("id") == node_id:
                d = n.get("data") or {}
                return d.get("type")
        return None

    def _build_webhook_node(
        self,
        *,
        node_id: str,
        target_agent: str,
        target_app_id: str,
        url: str,
        position_index: int,
        when: str,
    ) -> dict[str, Any]:
        # Authorization placeholder: real key is provisioned by W3 D4.
        env_key = (
            "DIFY_APP_API_KEY_"
            + target_agent.upper().replace("-", "_").replace(".", "_")
        )
        return {
            "id": node_id,
            "type": "custom",
            "position": {
                "x": 30 + position_index * _WEBHOOK_DX,
                "y": _WEBHOOK_BASE_Y,
            },
            "positionAbsolute": {
                "x": 30 + position_index * _WEBHOOK_DX,
                "y": _WEBHOOK_BASE_Y,
            },
            "width": _WEBHOOK_NODE_W,
            "height": _WEBHOOK_NODE_H,
            "sourcePosition": "right",
            "targetPosition": "left",
            "selected": False,
            "data": {
                "type": "http-request",
                "title": f"handoff to {target_agent}",
                "desc": (
                    f"factory-injected handoff webhook to specialist "
                    f"{target_agent} (dify_app_id={target_app_id or '?'}); "
                    f"when: {when or '-'}"
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
                    "target_agent": target_agent,
                    "target_dify_app_id": target_app_id or None,
                    "when": when,
                    "auth_env_key": env_key,
                },
            },
        }

    @staticmethod
    def _build_edge(
        source_id: str, target_id: str,
        source_type: str, target_type: str,
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
