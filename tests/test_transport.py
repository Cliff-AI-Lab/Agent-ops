"""Transport adapter tests (Batch F)."""
from __future__ import annotations

import httpx
import pytest
import respx

from app.core.transport import (
    HttpAdapter,
    LocalAdapter,
    TransportAuthError,
    TransportError,
    TransportRegistry,
    TransportRemoteError,
    TransportTimeoutError,
    get_transport_registry,
    reset_transport_registry,
)
from app.ontology import AgentContract, AgentGenerationMetadata


# ---------- Helpers ----------
def _make_contract(
    *,
    agent_id: str = "agent.demo",
    transport_type: str = "local",
    endpoint: str | None = None,
    auth_mode: str = "none",
    runtime_config: dict | None = None,
    latency_budget_ms: int | None = None,
) -> AgentContract:
    return AgentContract(
        agent_id=agent_id,
        version="0.1.0",
        name="Demo",
        description="demo",
        owner="test",
        transport_type=transport_type,  # type: ignore[arg-type]
        endpoint=endpoint,
        auth_mode=auth_mode,  # type: ignore[arg-type]
        runtime_config=runtime_config or {},
        latency_budget_ms=latency_budget_ms,
        is_generated=False,
        generation=AgentGenerationMetadata(),
    )


# ---------- LocalAdapter ----------
@pytest.mark.asyncio
async def test_local_adapter_invokes_sync_binding():
    adapter = LocalAdapter()
    adapter.register("agent.echo", lambda inputs: {"echoed": inputs.get("msg")})
    contract = _make_contract(agent_id="agent.echo", transport_type="local")
    out = await adapter.invoke(contract, {"msg": "hi"})
    assert out == {"echoed": "hi"}


@pytest.mark.asyncio
async def test_local_adapter_invokes_async_binding():
    async def handler(inputs):
        return {"async_value": inputs["x"] * 2}

    adapter = LocalAdapter({"agent.async": handler})
    contract = _make_contract(agent_id="agent.async")
    out = await adapter.invoke(contract, {"x": 21})
    assert out == {"async_value": 42}


@pytest.mark.asyncio
async def test_local_adapter_wraps_non_dict_return():
    adapter = LocalAdapter({"agent.scalar": lambda i: 99})
    contract = _make_contract(agent_id="agent.scalar")
    out = await adapter.invoke(contract, {})
    assert out == {"value": 99}


@pytest.mark.asyncio
async def test_local_adapter_missing_binding_raises():
    adapter = LocalAdapter()
    contract = _make_contract(agent_id="agent.absent")
    with pytest.raises(TransportError):
        await adapter.invoke(contract, {})


@pytest.mark.asyncio
async def test_local_adapter_timeout_raises_timeout_error():
    import asyncio

    async def slow(inputs):
        await asyncio.sleep(0.5)
        return {"never": "reached"}

    adapter = LocalAdapter({"agent.slow": slow})
    contract = _make_contract(agent_id="agent.slow")
    with pytest.raises(TransportTimeoutError):
        await adapter.invoke(contract, {}, timeout=0.05)


# ---------- HttpAdapter ----------
@pytest.mark.asyncio
async def test_http_adapter_happy_path():
    contract = _make_contract(
        agent_id="agent.web", transport_type="http",
        endpoint="https://example.com/agent",
    )
    with respx.mock(assert_all_called=True) as router:
        route = router.post("https://example.com/agent").mock(
            return_value=httpx.Response(200, json={"answer": 42}),
        )
        async with httpx.AsyncClient() as client:
            adapter = HttpAdapter(http_client=client)
            out = await adapter.invoke(contract, {"q": "what?"})
        assert out == {"answer": 42}
        assert route.called
        sent = route.calls.last.request
        # Body forwarded as JSON
        import json as _json
        assert _json.loads(sent.content.decode()) == {"q": "what?"}


@pytest.mark.asyncio
async def test_http_adapter_bearer_auth_header_set():
    contract = _make_contract(
        agent_id="agent.auth", transport_type="http",
        endpoint="https://example.com/agent",
        auth_mode="bearer",
        runtime_config={"bearer_token": "tok-123"},
    )
    with respx.mock(assert_all_called=True) as router:
        route = router.post("https://example.com/agent").mock(
            return_value=httpx.Response(200, json={"ok": True}),
        )
        async with httpx.AsyncClient() as client:
            adapter = HttpAdapter(http_client=client)
            await adapter.invoke(contract, {})
        assert route.calls.last.request.headers["Authorization"] == "Bearer tok-123"


@pytest.mark.asyncio
async def test_http_adapter_bearer_token_from_env(monkeypatch):
    monkeypatch.setenv("MY_REMOTE_TOKEN", "env-tok-xyz")
    contract = _make_contract(
        agent_id="agent.envauth", transport_type="http",
        endpoint="https://example.com/agent",
        auth_mode="bearer",
        runtime_config={"bearer_token_env": "MY_REMOTE_TOKEN"},
    )
    with respx.mock(assert_all_called=True) as router:
        route = router.post("https://example.com/agent").mock(
            return_value=httpx.Response(200, json={"ok": True}),
        )
        async with httpx.AsyncClient() as client:
            adapter = HttpAdapter(http_client=client)
            await adapter.invoke(contract, {})
        assert route.calls.last.request.headers["Authorization"] == "Bearer env-tok-xyz"


@pytest.mark.asyncio
async def test_http_adapter_missing_bearer_raises_auth_error():
    contract = _make_contract(
        agent_id="agent.noauth", transport_type="http",
        endpoint="https://example.com/agent",
        auth_mode="bearer",
        runtime_config={},  # no token configured
    )
    async with httpx.AsyncClient() as client:
        adapter = HttpAdapter(http_client=client)
        with pytest.raises(TransportAuthError):
            await adapter.invoke(contract, {})


@pytest.mark.asyncio
async def test_http_adapter_401_raises_auth_error():
    contract = _make_contract(
        agent_id="agent.x", transport_type="http",
        endpoint="https://example.com/agent",
    )
    with respx.mock() as router:
        router.post("https://example.com/agent").mock(
            return_value=httpx.Response(401, text="nope"),
        )
        async with httpx.AsyncClient() as client:
            adapter = HttpAdapter(http_client=client)
            with pytest.raises(TransportAuthError):
                await adapter.invoke(contract, {})


@pytest.mark.asyncio
async def test_http_adapter_5xx_raises_remote_error():
    contract = _make_contract(
        agent_id="agent.x", transport_type="http",
        endpoint="https://example.com/agent",
    )
    with respx.mock() as router:
        router.post("https://example.com/agent").mock(
            return_value=httpx.Response(503, text="busy"),
        )
        async with httpx.AsyncClient() as client:
            adapter = HttpAdapter(http_client=client)
            with pytest.raises(TransportRemoteError):
                await adapter.invoke(contract, {})


@pytest.mark.asyncio
async def test_http_adapter_non_json_response_raises_transport_error():
    contract = _make_contract(
        agent_id="agent.x", transport_type="http",
        endpoint="https://example.com/agent",
    )
    with respx.mock() as router:
        router.post("https://example.com/agent").mock(
            return_value=httpx.Response(200, text="not-json"),
        )
        async with httpx.AsyncClient() as client:
            adapter = HttpAdapter(http_client=client)
            with pytest.raises(TransportError):
                await adapter.invoke(contract, {})


@pytest.mark.asyncio
async def test_http_adapter_empty_endpoint_raises():
    contract = _make_contract(
        agent_id="agent.noendpoint", transport_type="http", endpoint=None,
    )
    async with httpx.AsyncClient() as client:
        adapter = HttpAdapter(http_client=client)
        with pytest.raises(TransportError):
            await adapter.invoke(contract, {})


@pytest.mark.asyncio
async def test_http_adapter_unsupported_auth_mode_raises():
    contract = _make_contract(
        agent_id="agent.x", transport_type="http",
        endpoint="https://example.com/agent",
        auth_mode="oauth",
    )
    async with httpx.AsyncClient() as client:
        adapter = HttpAdapter(http_client=client)
        with pytest.raises(TransportAuthError):
            await adapter.invoke(contract, {})


# ---------- TransportRegistry ----------
def test_registry_default_has_local_and_http():
    reset_transport_registry()
    reg = get_transport_registry()
    assert reg.has("local")
    assert reg.has("http")
    assert "local" in reg.types() and "http" in reg.types()
    reset_transport_registry()


def test_registry_can_register_custom_adapter():
    reg = TransportRegistry()
    custom = LocalAdapter({"a.b": lambda i: {"ok": True}})
    reg.register(custom)
    assert reg.get("local") is custom


def test_registry_rejects_empty_transport_type():
    reg = TransportRegistry()
    bad = LocalAdapter()
    bad.transport_type = ""  # type: ignore[misc]
    with pytest.raises(ValueError):
        reg.register(bad)


# ---------- CapabilityInvoker ↔ Transport integration ----------
@pytest.mark.asyncio
async def test_invoker_dispatches_http_agent_via_transport(monkeypatch):
    """When an agent-tier entry has transport_type=http, the invoker uses the adapter."""
    from app.core.workflow_engine.invoker import CapabilityInvoker
    from app.registry import agent_store as ags

    ags.reset_agent_store()
    contract = _make_contract(
        agent_id="agent.remote-demo", transport_type="http",
        endpoint="https://example.com/agent",
    )
    # Inject the contract into the live store for the invoker to find
    from pathlib import Path
    ags.get_agent_store().register_many([(contract, Path("inline://test"))])

    with respx.mock() as router:
        router.post("https://example.com/agent").mock(
            return_value=httpx.Response(200, json={"reply": "from-remote"}),
        )

        # Use a fresh transport registry pointing at the same default HttpAdapter
        # but with an httpx mounted on respx (default registry is fine since respx
        # patches httpx globally)
        reset_transport_registry()
        invoker = CapabilityInvoker(mock_when_missing=False)
        result = await invoker.invoke("agent.remote-demo", {"q": "hi"})
        assert result.error is None
        assert result.output == {"reply": "from-remote"}
        assert result.used_mock is False
        reset_transport_registry()


@pytest.mark.asyncio
async def test_invoker_falls_through_to_mock_for_local_agent():
    """transport_type='local' without binding still uses the mock path."""
    from app.core.workflow_engine.invoker import CapabilityInvoker
    from app.registry import agent_store as ags

    ags.reset_agent_store()
    contract = _make_contract(
        agent_id="agent.local-demo", transport_type="local",
    )
    from pathlib import Path
    ags.get_agent_store().register_many([(contract, Path("inline://test"))])

    invoker = CapabilityInvoker(mock_when_missing=True)
    result = await invoker.invoke("agent.local-demo", {})
    assert result.used_mock is True
    assert result.error is None
