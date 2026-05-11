"""Phase 10 W3 D1 - tests for MultiAgentDifyProjector.

Mock-based; no real LLM, no real Dify. Verifies:
1. One project() call calls build_fn + publisher.publish per specialist.
2. Mapping JSON {specialist_id -> dify_app_id} is written under log_dir.
3. Re-projection reuses prior mapping (same DifyPublisher behavior).
4. Build failure on one specialist does not stop the others.
5. Publish failure recorded as error in per-specialist result.
6. Slugify handles spaces / non-ascii names gracefully.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.core.pipelines.factory.ir.multi_agent import (
    GuardrailSpec,
    IndustryTag,
    MultiAgentSpec,
    SpecialistSpec,
    TriageSpec,
)
from app.delivery.multi_agent_dify_projector import (
    MultiAgentDifyProjector,
    ProjectionResult,
    SpecialistProjection,
)


@dataclass
class _StubPublishResult:
    """Minimal shape matching delivery.dify_publisher.PublishResult."""

    specimen_id: str
    dify_app_id: str
    status: str = "completed"
    is_first_deploy: bool = True
    deploy_count: int = 1
    yaml_sha256: str = "deadbeef"
    error: str = ""


def _make_spec(*specialists) -> MultiAgentSpec:
    return MultiAgentSpec(
        name="ad hoc test system",
        description="this is a multi-agent system used in unit tests",
        industry=IndustryTag(code="01", primary="通用"),
        triage=TriageSpec(
            name="Triage",
            system_prompt_id="prompt.triage.v1",
            initial_handoff_targets=[s["id"] for s in specialists],
        ),
        specialists=[
            SpecialistSpec(
                id=s["id"],
                name=s.get("name", s["id"]),
                agent_class=s.get("agent_class", "customer_service"),
                description=s.get("description", "specialist agent description"),
                nl_brief=s.get("nl_brief", f"build a {s['id']} agent for the system"),
                handoff_targets=s.get("handoff_targets", []),
                tools=s.get("tools", []),
            )
            for s in specialists
        ],
        guardrails=[GuardrailSpec(kind="relevance", description="block off-topic queries")],
    )


def _fake_build_factory(yaml_per_call: dict[str, str] | None = None,
                        failures: set[str] | None = None):
    """Returns an async build_fn that returns a fake Dify YAML per nl."""
    yaml_per_call = yaml_per_call or {}
    failures = failures or set()
    calls: list[str] = []

    async def _build(nl: str) -> dict[str, Any]:
        calls.append(nl)
        if nl in failures:
            raise RuntimeError(f"intentional build failure for {nl}")
        return {
            "outputs": {"dify": yaml_per_call.get(nl, f"version: '0.4.0'\nname: {nl}\n")},
            "dsl": yaml_per_call.get(nl, f"version: '0.4.0'\nname: {nl}\n"),
        }

    _build.calls = calls  # type: ignore[attr-defined]
    return _build


def _fake_publisher(app_ids: dict[str, str], errors: dict[str, str] | None = None):
    """publish(specimen, yaml, app_name=...) -> _StubPublishResult."""
    pub = MagicMock()
    errors = errors or {}

    def _publish(specimen_id: str, yaml: str, app_name: str | None = None):
        if specimen_id in errors:
            return _StubPublishResult(
                specimen_id=specimen_id,
                dify_app_id="",
                status="failed",
                error=errors[specimen_id],
            )
        return _StubPublishResult(
            specimen_id=specimen_id,
            dify_app_id=app_ids.get(specimen_id, f"app-{specimen_id}"),
        )

    pub.publish.side_effect = _publish
    return pub


@pytest.fixture
def log_dir(tmp_path: Path) -> Path:
    return tmp_path / "deploy_log"


def test_projects_one_specialist(log_dir: Path):
    spec = _make_spec({"id": "booking"})
    build = _fake_build_factory()
    pub = _fake_publisher({"ad_hoc_test_system.booking": "app-abc"})

    projector = MultiAgentDifyProjector(pub, build, log_dir=log_dir)
    result = asyncio.run(projector.project(spec))

    assert result.success_count == 1
    assert result.fail_count == 0
    assert result.specialists[0].dify_app_id == "app-abc"
    assert build.calls == [spec.specialists[0].nl_brief]
    pub.publish.assert_called_once()


def test_projects_all_specialists(log_dir: Path):
    spec = _make_spec({"id": "booking"}, {"id": "seat"}, {"id": "refund"})
    build = _fake_build_factory()
    pub = _fake_publisher({})

    projector = MultiAgentDifyProjector(pub, build, log_dir=log_dir)
    result = asyncio.run(projector.project(spec))

    assert result.success_count == 3
    assert {s.specialist_id for s in result.specialists} == {"booking", "seat", "refund"}
    assert len(build.calls) == 3


def test_mapping_written_to_log_dir(log_dir: Path):
    spec = _make_spec({"id": "booking"}, {"id": "seat"})
    build = _fake_build_factory()
    pub = _fake_publisher({})

    projector = MultiAgentDifyProjector(pub, build, log_dir=log_dir)
    result = asyncio.run(projector.project(spec))

    assert result.mapping_path is not None
    assert result.mapping_path.exists()
    data = json.loads(result.mapping_path.read_text(encoding="utf-8"))
    assert "booking" in data["specialists"]
    assert "seat" in data["specialists"]


def test_reprojection_preserves_prior_mapping_for_unchanged_specialists(log_dir: Path):
    spec1 = _make_spec({"id": "booking"}, {"id": "seat"})
    build1 = _fake_build_factory()
    pub1 = _fake_publisher({})
    projector = MultiAgentDifyProjector(pub1, build1, log_dir=log_dir)
    r1 = asyncio.run(projector.project(spec1))
    first_booking_app = next(
        s.dify_app_id for s in r1.specialists if s.specialist_id == "booking"
    )

    # second projection with same publisher returns same app_id
    spec2 = _make_spec({"id": "booking"})
    build2 = _fake_build_factory()
    pub2 = _fake_publisher({"ad_hoc_test_system.booking": first_booking_app})
    projector2 = MultiAgentDifyProjector(pub2, build2, log_dir=log_dir)
    r2 = asyncio.run(projector2.project(spec2))

    assert any(s.dify_app_id == first_booking_app for s in r2.specialists)

    # mapping file still has the seat entry from r1 (preserved)
    data = json.loads(r2.mapping_path.read_text(encoding="utf-8"))
    assert "seat" in data["specialists"]


def test_build_failure_does_not_halt_others(log_dir: Path):
    spec = _make_spec({"id": "ok1"}, {"id": "bad"}, {"id": "ok2"})
    bad_nl = next(s.nl_brief for s in spec.specialists if s.id == "bad")
    build = _fake_build_factory(failures={bad_nl})
    pub = _fake_publisher({})

    projector = MultiAgentDifyProjector(pub, build, log_dir=log_dir)
    result = asyncio.run(projector.project(spec))

    by_id = {s.specialist_id: s for s in result.specialists}
    assert by_id["ok1"].error == ""
    assert by_id["ok2"].error == ""
    assert "build failed" in by_id["bad"].error
    assert result.success_count == 2
    assert result.fail_count == 1


def test_publish_failure_recorded_as_error(log_dir: Path):
    spec = _make_spec({"id": "booking"})
    build = _fake_build_factory()
    pub = _fake_publisher({}, errors={"ad_hoc_test_system.booking": "dify import 400"})

    projector = MultiAgentDifyProjector(pub, build, log_dir=log_dir)
    result = asyncio.run(projector.project(spec))

    assert result.fail_count == 1
    assert "400" in result.specialists[0].error


def test_slugify_normalizes_spec_name(log_dir: Path):
    spec = _make_spec({"id": "x"})
    spec.name = "Air / Trip · System V2"
    build = _fake_build_factory()
    pub = _fake_publisher({})

    projector = MultiAgentDifyProjector(pub, build, log_dir=log_dir)
    result = asyncio.run(projector.project(spec))

    # slug strips spaces/slashes/center-dot; file lands cleanly
    assert result.mapping_path.exists()
    assert " " not in result.mapping_path.name
    assert "/" not in result.mapping_path.name


def test_short_summary_format(log_dir: Path):
    spec = _make_spec({"id": "x"}, {"id": "y"})
    build = _fake_build_factory()
    pub = _fake_publisher({})

    projector = MultiAgentDifyProjector(pub, build, log_dir=log_dir)
    result = asyncio.run(projector.project(spec))

    summary = result.short_summary()
    assert "system=" in summary
    assert "specialists=" in summary
    assert "2/2 ok" in summary


def test_explicit_system_slug_overrides_spec_name(log_dir: Path):
    spec = _make_spec({"id": "x"})
    build = _fake_build_factory()
    pub = _fake_publisher({})

    projector = MultiAgentDifyProjector(pub, build, log_dir=log_dir)
    result = asyncio.run(projector.project(spec, system_slug="custom-slug-123"))

    assert result.system_slug == "custom-slug-123"
    assert result.mapping_path.name == "custom-slug-123.multi-agent.json"
