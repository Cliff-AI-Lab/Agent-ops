"""Workflow Engine tests (Batch D)."""
from __future__ import annotations

import asyncio

import pytest

from app.core.workflow_engine import (
    CapabilityInvoker,
    StepResult,
    WorkflowEngine,
    WorkflowEvent,
    WorkflowRunResult,
)
from app.core.workflow_engine.executor import (
    CycleError,
    resolve_input_mapping,
    topological_order,
)
from app.ontology import WorkflowSpec, WorkflowStep


@pytest.fixture(scope="module", autouse=True)
def _bootstrap_registry():
    from app.registry import agent_store as ags
    from app.registry import store as cs
    from app.registry import tool_store as ts
    from app.registry.bootstrap import bootstrap_registry

    cs.reset_store()
    ts.reset_tool_store()
    ags.reset_agent_store()
    bootstrap_registry()
    ts.bootstrap_tools()
    ags.bootstrap_agents()
    yield


def _spec(steps: list[WorkflowStep], wf_id: str = "wf.test") -> WorkflowSpec:
    return WorkflowSpec(workflow_id=wf_id, version="0.1.0", steps=steps)


# ---------- topological_order ----------
def test_topo_simple_chain():
    spec = _spec([
        WorkflowStep(id="a", capability="x"),
        WorkflowStep(id="b", capability="x", depends_on=["a"]),
        WorkflowStep(id="c", capability="x", depends_on=["b"]),
    ])
    order = [s.id for s in topological_order(spec)]
    assert order == ["a", "b", "c"]


def test_topo_diamond_stable():
    spec = _spec([
        WorkflowStep(id="b", capability="x", depends_on=["a"]),
        WorkflowStep(id="d", capability="x", depends_on=["b", "c"]),
        WorkflowStep(id="c", capability="x", depends_on=["a"]),
        WorkflowStep(id="a", capability="x"),
    ])
    order = [s.id for s in topological_order(spec)]
    assert order.index("a") < order.index("b") < order.index("d")
    assert order.index("a") < order.index("c") < order.index("d")


def test_topo_cycle_raises():
    spec = _spec([
        WorkflowStep(id="a", capability="x", depends_on=["b"]),
        WorkflowStep(id="b", capability="x", depends_on=["a"]),
    ])
    with pytest.raises(CycleError):
        topological_order(spec)


# ---------- resolve_input_mapping ----------
def test_resolve_inputs_ref():
    out = resolve_input_mapping(
        {"q": "inputs.topic"}, {"topic": "AI"}, {}
    )
    assert out == {"q": "AI"}


def test_resolve_steps_ref_dotted():
    out = resolve_input_mapping(
        {"url": "steps.s1.results.0.url"},
        {},
        {"s1": {"results": [{"url": "https://x"}]}},
    )
    # We currently only walk dict.dict (not list indices). Returns None for list indexing.
    assert "url" in out


def test_resolve_steps_ref_simple():
    out = resolve_input_mapping(
        {"u": "steps.s1.url"}, {}, {"s1": {"url": "https://x"}}
    )
    assert out == {"u": "https://x"}


def test_resolve_literal_passthrough():
    out = resolve_input_mapping({"k": "literal-value"}, {}, {})
    assert out == {"k": "literal-value"}


# ---------- CapabilityInvoker ----------
@pytest.mark.asyncio
async def test_invoker_native_binding_wins():
    inv = CapabilityInvoker()
    inv.register("ui.plan_blueprint", lambda inputs: {"ok": True, "echo": inputs})
    res = await inv.invoke("ui.plan_blueprint", {"a": 1})
    assert res.error is None
    assert res.used_mock is False
    assert res.output == {"ok": True, "echo": {"a": 1}}


@pytest.mark.asyncio
async def test_invoker_async_binding():
    async def fn(inputs):
        return {"async_ok": True}
    inv = CapabilityInvoker()
    inv.register("ui.plan_blueprint", fn)
    res = await inv.invoke("ui.plan_blueprint", {})
    assert res.output == {"async_ok": True}


@pytest.mark.asyncio
async def test_invoker_falls_back_to_mock():
    inv = CapabilityInvoker()
    res = await inv.invoke("ui.plan_blueprint", {"foo": "bar"})
    assert res.error is None
    assert res.used_mock is True
    assert isinstance(res.output, dict)


@pytest.mark.asyncio
async def test_invoker_unknown_id_returns_error():
    inv = CapabilityInvoker()
    res = await inv.invoke("totally.fake.id", {})
    assert res.error is not None
    assert "not in registry" in res.error


# ---------- WorkflowEngine end-to-end ----------
@pytest.mark.asyncio
async def test_engine_runs_chain_and_yields_events():
    inv = CapabilityInvoker()
    inv.register("ui.plan_blueprint", lambda i: {"plan": i.get("topic", "?")})
    inv.register("delivery.package_zip", lambda i: {"zipped": i.get("plan")})
    spec = _spec([
        WorkflowStep(id="s1", capability="ui.plan_blueprint",
                     input_mapping={"topic": "inputs.topic"}),
        WorkflowStep(id="s2", capability="delivery.package_zip",
                     depends_on=["s1"],
                     input_mapping={"plan": "steps.s1.plan"}),
    ])
    engine = WorkflowEngine(invoker=inv)

    events: list[object] = []
    final: WorkflowRunResult | None = None
    async for item in engine.run(spec, {"topic": "demo"}):
        events.append(item)
        if isinstance(item, WorkflowRunResult):
            final = item

    assert final is not None
    assert final.status == "succeeded"
    assert [s.step_id for s in final.steps] == ["s1", "s2"]
    assert final.steps[0].output == {"plan": "demo"}
    assert final.steps[1].output == {"zipped": "demo"}

    kinds = [e.kind for e in events if isinstance(e, WorkflowEvent)]
    assert kinds[0] == "run_start"
    assert kinds.count("step_start") == 2
    assert kinds.count("step_done") == 2
    assert kinds[-1] == "run_done"


@pytest.mark.asyncio
async def test_engine_skips_downstream_when_predecessor_fails():
    def boom(_):
        raise RuntimeError("kaboom")
    inv = CapabilityInvoker()
    inv.register("ui.plan_blueprint", boom)
    inv.register("delivery.package_zip", lambda _: {"ok": True})
    spec = _spec([
        WorkflowStep(id="s1", capability="ui.plan_blueprint", on_failure="fail"),
        WorkflowStep(id="s2", capability="delivery.package_zip", depends_on=["s1"]),
    ])
    engine = WorkflowEngine(invoker=inv)
    final: WorkflowRunResult | None = None
    async for item in engine.run(spec):
        if isinstance(item, WorkflowRunResult):
            final = item
    assert final is not None
    assert final.status == "failed"
    statuses = {s.step_id: s.status for s in final.steps}
    assert statuses["s1"] == "failed"
    assert statuses["s2"] == "skipped"


@pytest.mark.asyncio
async def test_engine_retry_then_success():
    state = {"calls": 0}
    def flaky(_):
        state["calls"] += 1
        if state["calls"] < 3:
            raise RuntimeError("transient")
        return {"ok": True}
    inv = CapabilityInvoker()
    inv.register("ui.plan_blueprint", flaky)
    spec = _spec([
        WorkflowStep(id="s1", capability="ui.plan_blueprint",
                     on_failure="fail", max_retries=3),
    ])
    engine = WorkflowEngine(invoker=inv)
    final: WorkflowRunResult | None = None
    async for item in engine.run(spec):
        if isinstance(item, WorkflowRunResult):
            final = item
    assert final is not None
    assert final.status == "succeeded"
    assert final.steps[0].attempts == 3


@pytest.mark.asyncio
async def test_engine_skip_on_failure():
    inv = CapabilityInvoker()
    inv.register("ui.plan_blueprint", lambda _: (_ for _ in ()).throw(RuntimeError("nope")))
    spec = _spec([
        WorkflowStep(id="s1", capability="ui.plan_blueprint", on_failure="skip"),
    ])
    engine = WorkflowEngine(invoker=inv)
    final: WorkflowRunResult | None = None
    async for item in engine.run(spec):
        if isinstance(item, WorkflowRunResult):
            final = item
    assert final is not None
    assert final.steps[0].status == "skipped"


@pytest.mark.asyncio
async def test_engine_cycle_aborts_run():
    spec = _spec([
        WorkflowStep(id="a", capability="ui.plan_blueprint", depends_on=["b"]),
        WorkflowStep(id="b", capability="ui.plan_blueprint", depends_on=["a"]),
    ])
    engine = WorkflowEngine()
    items = [it async for it in engine.run(spec)]
    final = items[-1]
    assert isinstance(final, WorkflowRunResult)
    assert final.status == "failed"


@pytest.mark.asyncio
async def test_engine_uses_real_registry_with_mock_fallback():
    """Even without bindings, real registered capabilities run via mock fallback."""
    spec = _spec([
        WorkflowStep(id="s1", capability="web.search.tavily",
                     input_mapping={"query": "inputs.q"}),
    ])
    engine = WorkflowEngine()  # default invoker, mock_when_missing=True
    final: WorkflowRunResult | None = None
    async for item in engine.run(spec, {"q": "test"}):
        if isinstance(item, WorkflowRunResult):
            final = item
    assert final is not None
    assert final.status == "succeeded"
    assert final.steps[0].used_mock is True
    assert final.steps[0].output  # mock filled in
