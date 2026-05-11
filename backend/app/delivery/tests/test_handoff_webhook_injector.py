"""Phase 10 W3 D3 - tests for HandoffWebhookInjector."""

from __future__ import annotations

import yaml

import pytest

from app.core.pipelines.factory.compiler import DifyCompilerImpl
from app.core.pipelines.factory.ir import ResolvedDAG, ResolvedNode
from app.delivery.handoff_webhook_injector import (
    HandoffWebhookInjector,
    InjectionResult,
)
from app.delivery.multi_agent_dify_projector import HandoffEdgeMetadata


def _build_yaml(node_specs: list[tuple[str, str]]) -> str:
    """Use the real DifyCompiler so test YAML matches production shape."""
    dag = ResolvedDAG(
        intent_ref="test",
        nodes=[
            ResolvedNode(
                id=nid, asset_id=aid, asset_version="1.0.0",
                confidence=0.9, selection_reason="test",
            )
            for nid, aid in node_specs
        ],
        edges=[],
        target="dify",
    )
    return DifyCompilerImpl().compile(dag)


def _edge(from_agent: str, to_agent: str, *, target_id: str | None = None,
          when: str = "trigger condition") -> HandoffEdgeMetadata:
    return HandoffEdgeMetadata(
        from_agent=from_agent, to_agent=to_agent,
        from_dify_app_id="from-app-id",
        to_dify_app_id=target_id,
        when=when,
    )


def test_no_outgoing_edges_skipped():
    inj = HandoffWebhookInjector()
    src = _build_yaml([("s1", "atom.db.postgres.v1")])
    result = inj.inject("booking", src, [])
    assert result.did_inject is False
    assert "no outgoing handoff edges" in result.skipped_reason
    assert result.yaml_out == src  # unchanged


def test_single_handoff_injects_one_node():
    inj = HandoffWebhookInjector()
    src = _build_yaml([("s1", "atom.db.postgres.v1")])
    edges = [_edge("booking", "seat", target_id="app-seat-123")]
    result = inj.inject("booking", src, edges)

    assert result.did_inject is True
    assert len(result.injected) == 1
    assert result.injected[0].target_agent == "seat"
    assert result.injected[0].target_dify_app_id == "app-seat-123"
    assert result.yaml_chars_out > result.yaml_chars_in


def test_multiple_handoffs_chained_to_end():
    inj = HandoffWebhookInjector()
    src = _build_yaml([("s1", "atom.db.postgres.v1")])
    edges = [
        _edge("booking", "seat"),
        _edge("booking", "refund"),
        _edge("booking", "faq"),
    ]
    result = inj.inject("booking", src, edges)

    assert len(result.injected) == 3
    targets = [w.target_agent for w in result.injected]
    assert targets == ["seat", "refund", "faq"]


def test_injected_yaml_still_parses():
    inj = HandoffWebhookInjector()
    src = _build_yaml([("s1", "atom.db.postgres.v1")])
    edges = [_edge("booking", "seat")]
    result = inj.inject("booking", src, edges)
    parsed = yaml.safe_load(result.yaml_out)
    assert parsed is not None
    assert "workflow" in parsed
    assert "graph" in parsed["workflow"]


def test_injected_nodes_have_factory_metadata():
    inj = HandoffWebhookInjector()
    src = _build_yaml([("s1", "atom.db.postgres.v1")])
    edges = [_edge("booking", "seat", target_id="app-seat")]
    result = inj.inject("booking", src, edges)
    parsed = yaml.safe_load(result.yaml_out)
    handoff_nodes = [
        n for n in parsed["workflow"]["graph"]["nodes"]
        if isinstance(n.get("data"), dict) and "_factory_handoff" in n["data"]
    ]
    assert len(handoff_nodes) == 1
    hf = handoff_nodes[0]["data"]["_factory_handoff"]
    assert hf["target_agent"] == "seat"
    assert hf["target_dify_app_id"] == "app-seat"


def test_injected_node_data_type_is_http_request():
    inj = HandoffWebhookInjector()
    src = _build_yaml([("s1", "atom.db.postgres.v1")])
    edges = [_edge("booking", "seat")]
    result = inj.inject("booking", src, edges)
    parsed = yaml.safe_load(result.yaml_out)
    handoff_nodes = [
        n for n in parsed["workflow"]["graph"]["nodes"]
        if n.get("data", {}).get("title", "").startswith("handoff to")
    ]
    assert all(n["data"]["type"] == "http-request" for n in handoff_nodes)
    # top-level type stays custom (Dify React-Flow contract)
    assert all(n["type"] == "custom" for n in handoff_nodes)


def test_authorization_uses_env_placeholder_not_literal_key():
    """Bearer must reference an env var, never inline a real key."""
    inj = HandoffWebhookInjector()
    src = _build_yaml([("s1", "atom.db.postgres.v1")])
    edges = [_edge("booking", "seat")]
    result = inj.inject("booking", src, edges)
    parsed = yaml.safe_load(result.yaml_out)
    handoff_node = next(
        n for n in parsed["workflow"]["graph"]["nodes"]
        if n.get("data", {}).get("title", "").startswith("handoff to")
    )
    headers = handoff_node["data"]["headers"]
    assert "${DIFY_APP_API_KEY_SEAT}" in headers
    assert "sk-" not in headers  # no real key inlined


def test_chain_reconnects_to_end():
    """The last webhook must terminate at node_end so the workflow remains
    a valid Dify graph."""
    inj = HandoffWebhookInjector()
    src = _build_yaml([("s1", "atom.db.postgres.v1")])
    edges = [_edge("booking", "seat"), _edge("booking", "refund")]
    result = inj.inject("booking", src, edges)
    parsed = yaml.safe_load(result.yaml_out)
    edges_to_end = [
        e for e in parsed["workflow"]["graph"]["edges"]
        if e["target"] == "node_end"
    ]
    assert len(edges_to_end) == 1
    # the predecessor is the last handoff node we injected
    assert edges_to_end[0]["source"].startswith("handoff_booking_to_refund")


def test_filters_edges_by_source_agent():
    """Edges from OTHER agents must be ignored."""
    inj = HandoffWebhookInjector()
    src = _build_yaml([("s1", "atom.db.postgres.v1")])
    edges = [
        _edge("booking", "seat"),    # we want this
        _edge("triage", "seat"),     # NOT for booking
        _edge("seat", "refund"),     # NOT for booking
    ]
    result = inj.inject("booking", src, edges)
    assert len(result.injected) == 1
    assert result.injected[0].target_agent == "seat"


def test_url_targets_dify_service_api():
    inj = HandoffWebhookInjector(dify_base_url="https://example.dify.local")
    src = _build_yaml([("s1", "atom.db.postgres.v1")])
    result = inj.inject("booking", src, [_edge("booking", "seat")])
    parsed = yaml.safe_load(result.yaml_out)
    handoff_node = next(
        n for n in parsed["workflow"]["graph"]["nodes"]
        if n.get("data", {}).get("title", "").startswith("handoff to")
    )
    assert handoff_node["data"]["url"].startswith("https://example.dify.local/")
    assert "/v1/workflows/run" in handoff_node["data"]["url"]


def test_invalid_yaml_skipped_gracefully():
    inj = HandoffWebhookInjector()
    result = inj.inject(
        "booking",
        "this :: is :: not :: workflow yaml",
        [_edge("booking", "seat")],
    )
    assert result.did_inject is False
    assert result.skipped_reason


def test_yaml_missing_end_node_skipped():
    """If the upstream YAML doesn't have synthetic node_end (e.g. someone
    edited it out), injection bails out cleanly."""
    src_yaml = (
        "version: '0.4.0'\nkind: app\napp: {name: test, mode: workflow}\n"
        "workflow:\n"
        "  graph:\n"
        "    nodes:\n"
        "      - id: only\n"
        "        type: custom\n"
        "        data: {type: code, title: only}\n"
        "    edges: []\n"
    )
    inj = HandoffWebhookInjector()
    result = inj.inject("booking", src_yaml, [_edge("booking", "seat")])
    assert result.did_inject is False
    assert "node_end" in result.skipped_reason or "End" in result.skipped_reason


def test_node_id_includes_target_agent():
    inj = HandoffWebhookInjector()
    src = _build_yaml([("s1", "atom.db.postgres.v1")])
    result = inj.inject("booking", src, [_edge("booking", "seat")])
    assert result.injected[0].node_id.startswith("handoff_booking_to_seat_")
