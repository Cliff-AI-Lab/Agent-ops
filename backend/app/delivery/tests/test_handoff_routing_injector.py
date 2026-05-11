"""Phase 10 W5 D1 - tests for HandoffRoutingInjector."""

from __future__ import annotations

import yaml

import pytest

from app.core.pipelines.factory.compiler import DifyCompilerImpl
from app.core.pipelines.factory.ir import ResolvedDAG, ResolvedNode
from app.delivery.handoff_routing_injector import (
    HandoffRoutingInjector,
    InjectedRoute,
    RoutingInjectionResult,
)
from app.delivery.multi_agent_dify_projector import HandoffEdgeMetadata


def _yaml_with_llm() -> str:
    """Hand-built minimal Dify YAML with an LLM node + node_start/end."""
    return yaml.safe_dump({
        "version": "0.4.0",
        "kind": "app",
        "app": {"name": "triage_test", "mode": "workflow"},
        "workflow": {
            "graph": {
                "nodes": [
                    {"id": "node_start", "type": "custom",
                     "data": {"type": "start", "title": "Start"}},
                    {"id": "llm1", "type": "custom",
                     "data": {"type": "llm", "title": "Classifier"}},
                    {"id": "node_end", "type": "custom",
                     "data": {"type": "end", "title": "End"}},
                ],
                "edges": [
                    {"source": "node_start", "target": "llm1",
                     "id": "s-l", "type": "custom"},
                    {"source": "llm1", "target": "node_end",
                     "id": "l-e", "type": "custom"},
                ],
            },
        },
    }, allow_unicode=True, sort_keys=False)


def _yaml_without_llm() -> str:
    """A YAML that has no LLM node — routing should be skipped."""
    return yaml.safe_dump({
        "version": "0.4.0",
        "kind": "app",
        "app": {"name": "code_only", "mode": "workflow"},
        "workflow": {
            "graph": {
                "nodes": [
                    {"id": "node_start", "type": "custom",
                     "data": {"type": "start", "title": "Start"}},
                    {"id": "code1", "type": "custom",
                     "data": {"type": "code", "title": "Code"}},
                    {"id": "node_end", "type": "custom",
                     "data": {"type": "end", "title": "End"}},
                ],
                "edges": [
                    {"source": "node_start", "target": "code1",
                     "id": "s-c", "type": "custom"},
                    {"source": "code1", "target": "node_end",
                     "id": "c-e", "type": "custom"},
                ],
            },
        },
    }, allow_unicode=True, sort_keys=False)


def _edge(from_agent: str, to_agent: str, *, target_id: str | None = None,
          when: str = "trigger") -> HandoffEdgeMetadata:
    return HandoffEdgeMetadata(
        from_agent=from_agent, to_agent=to_agent,
        from_dify_app_id="from-app-id",
        to_dify_app_id=target_id,
        when=when,
    )


def test_no_outgoing_edges_skipped():
    inj = HandoffRoutingInjector()
    src = _yaml_with_llm()
    result = inj.inject_routing("triage", src, [])
    assert result.did_inject is False
    assert "no outgoing handoff edges" in result.skipped_reason


def test_no_llm_node_skipped():
    inj = HandoffRoutingInjector()
    src = _yaml_without_llm()
    result = inj.inject_routing("triage", src, [_edge("triage", "booking")])
    assert result.did_inject is False
    assert "llm node" in result.skipped_reason.lower()


def test_routing_creates_ifelse_with_one_case_per_edge():
    inj = HandoffRoutingInjector()
    src = _yaml_with_llm()
    edges = [
        _edge("triage", "booking", target_id="bid"),
        _edge("triage", "seat", target_id="sid"),
        _edge("triage", "refund", target_id="rid"),
    ]
    result = inj.inject_routing("triage", src, edges)
    assert result.did_inject is True
    assert len(result.routes) == 3

    parsed = yaml.safe_load(result.yaml_out)
    ifelse_nodes = [
        n for n in parsed["workflow"]["graph"]["nodes"]
        if n.get("data", {}).get("type") == "if-else"
    ]
    assert len(ifelse_nodes) == 1
    cases = ifelse_nodes[0]["data"]["cases"]
    assert len(cases) == 3
    case_targets = [c["conditions"][0]["value"] for c in cases]
    assert "booking" in case_targets
    assert "seat" in case_targets
    assert "refund" in case_targets


def test_each_case_condition_uses_contains_on_llm_text():
    inj = HandoffRoutingInjector()
    src = _yaml_with_llm()
    result = inj.inject_routing("triage", src, [_edge("triage", "seat")])
    parsed = yaml.safe_load(result.yaml_out)
    ifelse = next(
        n for n in parsed["workflow"]["graph"]["nodes"]
        if n.get("data", {}).get("type") == "if-else"
    )
    cond = ifelse["data"]["cases"][0]["conditions"][0]
    assert cond["comparison_operator"] == "contains"
    assert cond["variable_selector"] == ["llm1", "text"]
    assert cond["value"] == "seat"


def test_webhook_per_branch_attached_to_ifelse():
    inj = HandoffRoutingInjector()
    src = _yaml_with_llm()
    edges = [_edge("triage", "booking"), _edge("triage", "seat")]
    result = inj.inject_routing("triage", src, edges)
    parsed = yaml.safe_load(result.yaml_out)
    handoff_webhooks = [
        n for n in parsed["workflow"]["graph"]["nodes"]
        if n.get("data", {}).get("title", "").startswith("handoff to")
    ]
    assert len(handoff_webhooks) == 2


def test_edge_sourcehandle_uses_case_id():
    inj = HandoffRoutingInjector()
    src = _yaml_with_llm()
    result = inj.inject_routing("triage", src, [_edge("triage", "booking")])
    parsed = yaml.safe_load(result.yaml_out)
    ifelse_id = next(
        n["id"] for n in parsed["workflow"]["graph"]["nodes"]
        if n.get("data", {}).get("type") == "if-else"
    )
    case_id = result.routes[0].case_id
    edge_to_webhook = next(
        e for e in parsed["workflow"]["graph"]["edges"]
        if e["source"] == ifelse_id and e.get("sourceHandle") == case_id
    )
    assert edge_to_webhook is not None


def test_else_branch_drops_to_end():
    inj = HandoffRoutingInjector()
    src = _yaml_with_llm()
    result = inj.inject_routing("triage", src, [_edge("triage", "booking")])
    parsed = yaml.safe_load(result.yaml_out)
    ifelse_id = next(
        n["id"] for n in parsed["workflow"]["graph"]["nodes"]
        if n.get("data", {}).get("type") == "if-else"
    )
    else_edges = [
        e for e in parsed["workflow"]["graph"]["edges"]
        if e["source"] == ifelse_id
        and e.get("sourceHandle") == "false"
        and e["target"] == "node_end"
    ]
    assert len(else_edges) == 1


def test_each_webhook_connects_to_end():
    inj = HandoffRoutingInjector()
    src = _yaml_with_llm()
    result = inj.inject_routing("triage", src, [
        _edge("triage", "booking"), _edge("triage", "seat"),
    ])
    parsed = yaml.safe_load(result.yaml_out)
    edges_to_end = [
        e for e in parsed["workflow"]["graph"]["edges"]
        if e["target"] == "node_end"
    ]
    # 2 webhooks + 1 else branch
    assert len(edges_to_end) == 3


def test_authorization_uses_env_placeholder():
    inj = HandoffRoutingInjector()
    src = _yaml_with_llm()
    result = inj.inject_routing("triage", src, [_edge("triage", "seat")])
    parsed = yaml.safe_load(result.yaml_out)
    webhook = next(
        n for n in parsed["workflow"]["graph"]["nodes"]
        if n.get("data", {}).get("title", "").startswith("handoff to")
    )
    assert "${DIFY_APP_API_KEY_SEAT}" in webhook["data"]["headers"]


def test_routing_mode_marker_in_factory_handoff():
    inj = HandoffRoutingInjector()
    src = _yaml_with_llm()
    result = inj.inject_routing("triage", src, [_edge("triage", "booking")])
    parsed = yaml.safe_load(result.yaml_out)
    webhook = next(
        n for n in parsed["workflow"]["graph"]["nodes"]
        if n.get("data", {}).get("title", "").startswith("handoff to")
    )
    assert webhook["data"]["_factory_handoff"]["routing_mode"] == "switch"


def test_factory_routing_metadata_on_ifelse():
    inj = HandoffRoutingInjector()
    src = _yaml_with_llm()
    result = inj.inject_routing("triage", src, [_edge("triage", "booking")])
    parsed = yaml.safe_load(result.yaml_out)
    ifelse = next(
        n for n in parsed["workflow"]["graph"]["nodes"]
        if n.get("data", {}).get("type") == "if-else"
    )
    meta = ifelse["data"]["_factory_routing"]
    assert meta["source_agent"] == "triage"
    assert meta["llm_node_id"] == "llm1"
    assert meta["case_count"] == 1


def test_invalid_yaml_skipped():
    inj = HandoffRoutingInjector()
    result = inj.inject_routing("triage", "::: not yaml :::", [_edge("triage", "x")])
    assert result.did_inject is False


def test_edges_from_other_agents_filtered():
    inj = HandoffRoutingInjector()
    src = _yaml_with_llm()
    edges = [
        _edge("triage", "booking"),
        _edge("booking", "seat"),  # not for triage
    ]
    result = inj.inject_routing("triage", src, edges)
    assert len(result.routes) == 1
    assert result.routes[0].target_agent == "booking"


# ----- W5 D2: strict condition_mode -----


def test_strict_mode_uses_is_operator():
    inj = HandoffRoutingInjector()
    src = _yaml_with_llm()
    result = inj.inject_routing(
        "triage", src, [_edge("triage", "booking")],
        condition_mode="is",
    )
    parsed = yaml.safe_load(result.yaml_out)
    ifelse = next(
        n for n in parsed["workflow"]["graph"]["nodes"]
        if n.get("data", {}).get("type") == "if-else"
    )
    cond = ifelse["data"]["cases"][0]["conditions"][0]
    assert cond["comparison_operator"] == "is"
    assert ifelse["data"]["_factory_routing"]["condition_mode"] == "is"
    assert ifelse["data"]["_factory_routing"]["operator"] == "is"


def test_default_mode_keeps_contains_operator():
    inj = HandoffRoutingInjector()
    src = _yaml_with_llm()
    result = inj.inject_routing("triage", src, [_edge("triage", "booking")])
    parsed = yaml.safe_load(result.yaml_out)
    ifelse = next(
        n for n in parsed["workflow"]["graph"]["nodes"]
        if n.get("data", {}).get("type") == "if-else"
    )
    cond = ifelse["data"]["cases"][0]["conditions"][0]
    assert cond["comparison_operator"] == "contains"
    assert ifelse["data"]["_factory_routing"]["condition_mode"] == "contains"


def test_strict_mode_appends_prompt_constraint_to_llm():
    """Strict mode must append an explicit allowlist instruction to the LLM."""
    inj = HandoffRoutingInjector()
    src = _yaml_with_llm()
    targets = ["booking", "seat", "refund"]
    edges = [_edge("triage", t) for t in targets]
    result = inj.inject_routing(
        "triage", src, edges, condition_mode="is",
    )
    parsed = yaml.safe_load(result.yaml_out)
    llm = next(
        n for n in parsed["workflow"]["graph"]["nodes"] if n["id"] == "llm1"
    )
    # constraint added to prompt_template
    prompts = llm["data"].get("prompt_template", [])
    constraint = next(
        (p for p in prompts if "FACTORY ROUTING CONSTRAINT" in p.get("text", "")),
        None,
    )
    assert constraint is not None
    for t in targets:
        assert t in constraint["text"]
    # __none__ fallback for else branch
    assert "__none__" in constraint["text"]
    # node-level marker
    marker = llm["data"]["_factory_routing_prompt"]
    assert marker["appended"] is True
    assert marker["candidates"] == targets


def test_contains_mode_does_not_touch_prompt():
    """Default contains mode is non-invasive on the LLM prompt."""
    inj = HandoffRoutingInjector()
    src = _yaml_with_llm()
    result = inj.inject_routing("triage", src, [_edge("triage", "booking")])
    parsed = yaml.safe_load(result.yaml_out)
    llm = next(
        n for n in parsed["workflow"]["graph"]["nodes"] if n["id"] == "llm1"
    )
    prompts = llm["data"].get("prompt_template", [])
    assert not any("FACTORY ROUTING CONSTRAINT" in p.get("text", "")
                   for p in prompts)
    assert "_factory_routing_prompt" not in llm["data"]


def test_fallback_agent_else_routes_to_fallback_webhook():
    inj = HandoffRoutingInjector()
    src = _yaml_with_llm()
    edges = [
        _edge("triage", "booking"),
        _edge("triage", "seat"),
        _edge("triage", "faq"),
    ]
    result = inj.inject_routing(
        "triage", src, edges, fallback_agent="faq",
    )
    parsed = yaml.safe_load(result.yaml_out)
    ifelse_id = next(
        n["id"] for n in parsed["workflow"]["graph"]["nodes"]
        if n.get("data", {}).get("type") == "if-else"
    )
    fallback_wh_id = next(
        r.webhook_node_id for r in result.routes if r.target_agent == "faq"
    )
    else_edges = [
        e for e in parsed["workflow"]["graph"]["edges"]
        if e["source"] == ifelse_id and e.get("sourceHandle") == "false"
    ]
    assert len(else_edges) == 1
    assert else_edges[0]["target"] == fallback_wh_id
    assert else_edges[0]["data"]["targetType"] == "http-request"


def test_no_fallback_keeps_else_to_end():
    inj = HandoffRoutingInjector()
    src = _yaml_with_llm()
    result = inj.inject_routing("triage", src, [_edge("triage", "booking")])
    parsed = yaml.safe_load(result.yaml_out)
    ifelse_id = next(
        n["id"] for n in parsed["workflow"]["graph"]["nodes"]
        if n.get("data", {}).get("type") == "if-else"
    )
    else_edge = next(
        e for e in parsed["workflow"]["graph"]["edges"]
        if e["source"] == ifelse_id and e.get("sourceHandle") == "false"
    )
    assert else_edge["target"] == "node_end"


def test_fallback_agent_not_in_outgoing_skipped():
    inj = HandoffRoutingInjector()
    src = _yaml_with_llm()
    result = inj.inject_routing(
        "triage", src, [_edge("triage", "booking")],
        fallback_agent="ghost",
    )
    assert result.did_inject is False
    assert "fallback_agent" in result.skipped_reason
    assert "ghost" in result.skipped_reason


def test_fallback_metadata_in_factory_routing():
    inj = HandoffRoutingInjector()
    src = _yaml_with_llm()
    edges = [_edge("triage", "booking"), _edge("triage", "faq")]
    result = inj.inject_routing("triage", src, edges, fallback_agent="faq")
    parsed = yaml.safe_load(result.yaml_out)
    ifelse = next(
        n for n in parsed["workflow"]["graph"]["nodes"]
        if n.get("data", {}).get("type") == "if-else"
    )
    meta = ifelse["data"]["_factory_routing"]
    assert meta["fallback_agent"] == "faq"
    assert meta["fallback_webhook_id"] is not None


def test_fallback_with_strict_mode():
    """Strict + fallback work together."""
    inj = HandoffRoutingInjector()
    src = _yaml_with_llm()
    edges = [_edge("triage", "booking"), _edge("triage", "faq")]
    result = inj.inject_routing(
        "triage", src, edges,
        condition_mode="is", fallback_agent="faq",
    )
    parsed = yaml.safe_load(result.yaml_out)
    ifelse = next(
        n for n in parsed["workflow"]["graph"]["nodes"]
        if n.get("data", {}).get("type") == "if-else"
    )
    meta = ifelse["data"]["_factory_routing"]
    assert meta["operator"] == "is"
    assert meta["fallback_agent"] == "faq"
    # LLM still gets the prompt constraint
    llm = next(n for n in parsed["workflow"]["graph"]["nodes"] if n["id"] == "llm1")
    prompts = llm["data"]["prompt_template"]
    assert any("FACTORY ROUTING CONSTRAINT" in p["text"] for p in prompts)


def test_strict_mode_handles_llm_node_without_prompt_template():
    """If LLM node lacks prompt_template, helper must add one cleanly."""
    raw = {
        "version": "0.4.0", "kind": "app",
        "app": {"name": "x", "mode": "workflow"},
        "workflow": {"graph": {
            "nodes": [
                {"id": "node_start", "type": "custom",
                 "data": {"type": "start"}},
                {"id": "llm1", "type": "custom",
                 "data": {"type": "llm"}},  # no prompt_template
                {"id": "node_end", "type": "custom",
                 "data": {"type": "end"}},
            ],
            "edges": [{"source": "llm1", "target": "node_end",
                       "id": "l-e", "type": "custom"}],
        }},
    }
    src = yaml.safe_dump(raw, allow_unicode=True, sort_keys=False)
    inj = HandoffRoutingInjector()
    result = inj.inject_routing(
        "triage", src, [_edge("triage", "booking")],
        condition_mode="is",
    )
    assert result.did_inject is True
    parsed = yaml.safe_load(result.yaml_out)
    llm = next(n for n in parsed["workflow"]["graph"]["nodes"] if n["id"] == "llm1")
    prompts = llm["data"]["prompt_template"]
    assert any("FACTORY ROUTING CONSTRAINT" in p["text"] for p in prompts)
