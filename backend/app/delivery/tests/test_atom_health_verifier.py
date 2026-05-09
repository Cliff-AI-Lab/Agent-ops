"""Phase 9 W2 - tests for AtomHealthVerifier."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.delivery.atom_health_verifier import (
    AtomHealthVerifier,
    HealthReport,
)


def _atom(
    asset_id: str = "atom.test.v1",
    *,
    test_cases: int = 2,
    not_applicable: int = 1,
    description: str = "x" * 30,
    tags: list[str] | None = None,
    dify_template: str | None = None,
    dify_not_supported: bool = False,
) -> MagicMock:
    """Build an atom-like object exposing the attributes the verifier reads."""
    a = MagicMock()
    a.asset_id = asset_id
    a.test_cases = [MagicMock() for _ in range(test_cases)]
    a.NOT_applicable = [f"limit_{i}" for i in range(not_applicable)]
    a.description = description
    a.tags = tags or ["t1"]

    proj = MagicMock()
    proj.template = dify_template
    proj.not_supported = dify_not_supported
    a.projections = {"dify": proj}
    return a


def _good_template() -> str:
    return (
        "type: code\n"
        "title: {{ atom.asset_id | tojson }}\n"
        "desc: {{ (node.selection_reason or '')[:200] | tojson }}\n"
    )


@pytest.fixture
def verifier(tmp_path: Path) -> AtomHealthVerifier:
    return AtomHealthVerifier(log_dir=tmp_path / ".factory_health_log")


def test_perfect_atom_scores_one(verifier: AtomHealthVerifier):
    atom = _atom(dify_template=_good_template())
    report = verifier.verify(atom)
    assert report.pass_rate == pytest.approx(1.0)
    assert report.passed_count == 5
    assert all(c.passed for c in report.checks)


def test_log_file_written(verifier: AtomHealthVerifier):
    atom = _atom(asset_id="atom.foo.v1", dify_template=_good_template())
    verifier.verify(atom)
    p = verifier.log_path("atom.foo.v1")
    assert p.exists()
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["asset_id"] == "atom.foo.v1"
    assert data["pass_rate"] == 1.0
    assert len(data["checks"]) == 5


def test_too_few_test_cases_reduces_score(verifier: AtomHealthVerifier):
    atom = _atom(test_cases=1, dify_template=_good_template())
    report = verifier.verify(atom)
    # 1.0 - 0.20 (test_cases_ok weight) = 0.80
    assert report.pass_rate == pytest.approx(0.80)
    bad = next(c for c in report.checks if c.name == "test_cases_ok")
    assert not bad.passed


def test_legacy_placeholder_template_fails(verifier: AtomHealthVerifier):
    atom = _atom(dify_template="# Phase 1 W3 placeholder")
    report = verifier.verify(atom)
    bad = next(c for c in report.checks if c.name == "dify_template_ok")
    assert not bad.passed
    assert "legacy" in bad.detail
    # 1.0 - 0.30 = 0.70
    assert report.pass_rate == pytest.approx(0.70)


def test_broken_jinja_template_fails(verifier: AtomHealthVerifier):
    atom = _atom(dify_template="type: code\ntitle: {{ undefined_var }}")
    report = verifier.verify(atom)
    bad = next(c for c in report.checks if c.name == "dify_template_ok")
    assert not bad.passed
    assert "render failed" in bad.detail


def test_not_supported_dify_atom_passes_template_check(verifier: AtomHealthVerifier):
    """Schedule cron is not_supported in Dify - that's a valid skip, not a fail."""
    atom = _atom(dify_template=None, dify_not_supported=True)
    report = verifier.verify(atom)
    tmpl = next(c for c in report.checks if c.name == "dify_template_ok")
    assert tmpl.passed
    assert "not_supported" in tmpl.detail


def test_short_description_fails_metadata(verifier: AtomHealthVerifier):
    atom = _atom(description="too short", dify_template=_good_template())
    report = verifier.verify(atom)
    meta = next(c for c in report.checks if c.name == "metadata_ok")
    assert not meta.passed
    assert "desc>=20=False" in meta.detail


def test_empty_not_applicable_fails(verifier: AtomHealthVerifier):
    atom = _atom(not_applicable=0, dify_template=_good_template())
    report = verifier.verify(atom)
    na = next(c for c in report.checks if c.name == "not_applicable_ok")
    assert not na.passed


def test_verify_path_handles_loader_failure(verifier: AtomHealthVerifier, tmp_path: Path):
    bogus = tmp_path / "broken.yaml"
    bogus.write_text("asset_id: atom.broken.v1\nthis_is: not_valid_atom_yaml: bad", encoding="utf-8")
    report = verifier.verify_path(bogus)
    assert report.pass_rate == 0.0
    yaml_check = next(c for c in report.checks if c.name == "yaml_loadable")
    assert not yaml_check.passed


def test_verify_all_returns_one_report_per_atom(verifier: AtomHealthVerifier):
    atoms = {
        "atom.a.v1": _atom("atom.a.v1", dify_template=_good_template()),
        "atom.b.v1": _atom("atom.b.v1", dify_template=_good_template(), test_cases=1),
    }
    reports = verifier.verify_all(atoms)
    assert {r.asset_id for r in reports} == {"atom.a.v1", "atom.b.v1"}
    by_id = {r.asset_id: r for r in reports}
    assert by_id["atom.a.v1"].pass_rate == 1.0
    assert by_id["atom.b.v1"].pass_rate == pytest.approx(0.80)


def test_read_log_round_trip(verifier: AtomHealthVerifier):
    atom = _atom(asset_id="atom.rt.v1", dify_template=_good_template())
    report = verifier.verify(atom)
    raw = verifier.read_log("atom.rt.v1")
    assert raw is not None
    assert raw["pass_rate"] == report.pass_rate
    assert raw["asset_id"] == "atom.rt.v1"


def test_check_weights_sum_to_one():
    total = sum(AtomHealthVerifier.CHECK_WEIGHTS.values())
    assert total == pytest.approx(1.0), (
        "weights must sum to 1.0 so pass_rate is in [0,1]"
    )
