"""Phase 9 W1 Day 3 - tests for the score signal in rank_candidates.

Verifies:
1. score_service=None preserves Phase 8 behavior (no signal, same confidence).
2. cold-start score=0 leaves confidence unchanged but still emits trace.
3. mid-tier score lifts confidence multiplicatively.
4. perfect score=1.0 lifts confidence by exactly SCORE_SIGNAL_ALPHA.
5. result is clamped to [0, 1] (no overflow).
6. single-candidate path also applies the signal.
7. parse-fail fallback path also applies the signal (still observable).
8. AtomScoreService raising an exception degrades gracefully (no crash).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.pipelines.factory.ir.intent import StepSpec
from app.core.pipelines.factory.resolver.rank import (
    SCORE_SIGNAL_ALPHA,
    _apply_score_signal,
    _Selection,
    rank_candidates,
)
from app.delivery.atom_score_service import AtomScore, AtomScoreService


def _atom(asset_id: str, subcategory: str = "DB"):
    a = MagicMock()
    a.asset_id = asset_id
    a.subcategory = subcategory
    a.NOT_applicable = []
    a.tags = []
    a.description = "test atom"
    return a


def _step(step_id: str = "s1", verb: str = "test verb"):
    return StepSpec(
        id=step_id,
        verb=verb,
        inputs={},
        expected_output_kind="json_object",
        constraints={},
        suggested_subcategory="DB",
    )


def _scoring_service(scores: dict[str, float]) -> AtomScoreService:
    """Build a service whose .score(asset_id) returns the canned value."""

    svc = MagicMock(spec=AtomScoreService)

    def _score(asset_id: str) -> AtomScore:
        v = scores.get(asset_id, 0.0)
        return AtomScore(
            atom_id=asset_id,
            score=v,
            has_history=v > 0.0,
            static_pass_rate=None,
            stats=None,
            components={"mock": v},
            explanation="mock score",
        )

    svc.score.side_effect = _score
    return svc


def test_no_service_preserves_confidence():
    sel = _Selection(atom=_atom("atom.foo.v1"), confidence=0.6, reason="r")
    out = _apply_score_signal(sel, _step(), score_service=None)
    assert out.confidence == 0.6


def test_cold_start_zero_score_leaves_confidence_unchanged():
    svc = _scoring_service({"atom.foo.v1": 0.0})
    sel = _Selection(atom=_atom("atom.foo.v1"), confidence=0.6, reason="r")
    out = _apply_score_signal(sel, _step(), svc)
    # 0.6 * (1 + 0.2*0) = 0.6
    assert out.confidence == pytest.approx(0.6)


def test_mid_tier_score_lifts_confidence_multiplicatively():
    svc = _scoring_service({"atom.foo.v1": 0.5})
    sel = _Selection(atom=_atom("atom.foo.v1"), confidence=0.6, reason="r")
    out = _apply_score_signal(sel, _step(), svc)
    # 0.6 * (1 + 0.2*0.5) = 0.66
    assert out.confidence == pytest.approx(0.66)


def test_perfect_score_lifts_by_exactly_alpha():
    svc = _scoring_service({"atom.foo.v1": 1.0})
    sel = _Selection(atom=_atom("atom.foo.v1"), confidence=0.5, reason="r")
    out = _apply_score_signal(sel, _step(), svc)
    # 0.5 * (1 + 0.2) = 0.6
    assert out.confidence == pytest.approx(0.5 * (1 + SCORE_SIGNAL_ALPHA))


def test_signal_clamps_to_one():
    svc = _scoring_service({"atom.foo.v1": 1.0})
    sel = _Selection(atom=_atom("atom.foo.v1"), confidence=0.95, reason="r")
    out = _apply_score_signal(sel, _step(), svc)
    # 0.95 * 1.2 = 1.14 -> clamp to 1.0
    assert out.confidence == 1.0


def test_signal_does_not_change_atom_or_reason():
    svc = _scoring_service({"atom.foo.v1": 0.5})
    sel = _Selection(
        atom=_atom("atom.foo.v1"), confidence=0.6, reason="original",
        llm_task_type="x", llm_size="中",
    )
    out = _apply_score_signal(sel, _step(), svc)
    assert out.atom.asset_id == "atom.foo.v1"
    assert out.reason == "original"
    assert out.llm_task_type == "x"
    assert out.llm_size == "中"


def test_score_service_exception_degrades_gracefully():
    svc = MagicMock(spec=AtomScoreService)
    svc.score.side_effect = RuntimeError("db locked")
    sel = _Selection(atom=_atom("atom.foo.v1"), confidence=0.7, reason="r")
    out = _apply_score_signal(sel, _step(), svc)
    # service crash must not corrupt the selection
    assert out.confidence == 0.7
    assert out.atom.asset_id == "atom.foo.v1"


@pytest.mark.asyncio
async def test_rank_candidates_single_candidate_applies_signal():
    """Single-candidate fast path also weighted by score."""
    atom = _atom("atom.solo.v1")
    candidates = [(atom, 0.4)]
    svc = _scoring_service({"atom.solo.v1": 0.5})

    out = await rank_candidates(
        step=_step(),
        candidates=candidates,
        llm_client=MagicMock(),
        model="ignored-because-single-candidate",
        score_service=svc,
    )
    # single-candidate base = min(0.95, max(0.75, 0.4 + 0.6)) = 0.95
    # signal = 0.95 * 1.1 = 1.045 -> clamp 1.0
    assert out.atom.asset_id == "atom.solo.v1"
    assert out.confidence == 1.0


@pytest.mark.asyncio
async def test_rank_candidates_no_score_service_unchanged():
    """Phase 8 baseline: when score_service is None, confidence stays the same."""
    atom = _atom("atom.solo.v1")
    candidates = [(atom, 0.4)]
    out = await rank_candidates(
        step=_step(),
        candidates=candidates,
        llm_client=MagicMock(),
        model="ignored",
        score_service=None,
    )
    assert out.confidence == pytest.approx(0.95)  # base: max(0.75, 0.4+0.6)


@pytest.mark.asyncio
async def test_rank_candidates_parse_fail_fallback_still_signals():
    """If LLM JSON parse fails, fallback path should still apply score signal."""
    atom_a = _atom("atom.a.v1")
    atom_b = _atom("atom.b.v1")
    candidates = [(atom_a, 0.6), (atom_b, 0.4)]

    llm = MagicMock()
    response = MagicMock()
    response.content = "not-json garbage"
    llm.chat = AsyncMock(return_value=response)

    svc = _scoring_service({"atom.a.v1": 0.5})

    out = await rank_candidates(
        step=_step(),
        candidates=candidates,
        llm_client=llm,
        model="m",
        score_service=svc,
    )
    # fell back to top recall (atom.a, score=0.6), then signal: 0.6 * 1.1 = 0.66
    assert out.atom.asset_id == "atom.a.v1"
    assert out.confidence == pytest.approx(0.66)
