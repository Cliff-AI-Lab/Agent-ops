"""Phase 9 W1 Day 1 - tests for AtomScoreService.

Verifies:
1. AtomUsageView SQL aggregates wiki_articles + qa_runs correctly.
2. score() returns cold-start fallback when no usage history.
3. score() formula A combines invocations + success + recency.
4. rank() filters by layer prefix and sorts descending.
5. Edge cases: all-fail atom, never-used atom, unknown atom.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.delivery.atom_score_service import (
    ATOM_USAGE_VIEW_SQL,
    AtomScoreService,
)


def _seed_db(path: Path) -> None:
    """Create the minimal V2.5 schema slice this service reads from."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE wiki_articles (
            system_slug TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            industry_code TEXT,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            body_md TEXT NOT NULL,
            scenarios_json TEXT NOT NULL,
            fits_json TEXT NOT NULL,
            specialist_ids_json TEXT,
            atom_ids_json TEXT,
            qa_sign_off TEXT,
            qa_certified_at TEXT,
            qa_run_id INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE qa_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            stage TEXT NOT NULL,
            agent_role TEXT NOT NULL,
            verdict TEXT NOT NULL,
            pass_count INT DEFAULT 0,
            fail_count INT DEFAULT 0,
            cases_json TEXT,
            report_md TEXT,
            created_at TEXT NOT NULL
        );
        """
    )
    conn.commit()
    conn.close()


def _add_wiki(
    path: Path,
    *,
    slug: str,
    session_id: str,
    industry: str,
    atom_ids: list[str],
    qa_sign_off: str | None = None,
    days_ago: int = 0,
) -> None:
    ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(timespec="seconds")
    conn = sqlite3.connect(path)
    conn.execute(
        """
        INSERT INTO wiki_articles (
            system_slug, session_id, industry_code, title, summary, body_md,
            scenarios_json, fits_json, atom_ids_json, qa_sign_off, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            slug, session_id, industry, slug, "summary", "body",
            "[]", "[]", json.dumps(atom_ids),
            qa_sign_off, ts, ts,
        ),
    )
    conn.commit()
    conn.close()


def _add_qa_run(
    path: Path,
    *,
    session_id: str,
    stage: str = "uat",
    verdict: str = "pass",
    pass_count: int = 1,
    fail_count: int = 0,
) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        INSERT INTO qa_runs (
            session_id, stage, agent_role, verdict,
            pass_count, fail_count, cases_json, report_md, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (
            session_id, stage, "tester", verdict,
            pass_count, fail_count, "[]", "report",
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ),
    )
    conn.commit()
    conn.close()


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "test_harness.db"
    _seed_db(p)
    return p


def test_view_sql_yields_no_rows_on_empty_db(db_path: Path):
    svc = AtomScoreService(db_path=db_path)
    assert svc.all_stats() == []


def test_view_sql_aggregates_atoms_across_specimens(db_path: Path):
    _add_wiki(db_path, slug="banking", session_id="s1", industry="01",
              atom_ids=["atom.llm.chat.v1", "atom.notify.dingtalk.v1"])
    _add_wiki(db_path, slug="airline", session_id="s2", industry="05",
              atom_ids=["atom.llm.chat.v1", "atom.db.postgres.v1"])
    _add_qa_run(db_path, session_id="s1", verdict="pass", pass_count=3, fail_count=1)
    _add_qa_run(db_path, session_id="s2", verdict="pass", pass_count=2, fail_count=0)

    svc = AtomScoreService(db_path=db_path)
    by_id = {s.atom_id: s for s in svc.all_stats()}

    assert by_id["atom.llm.chat.v1"].specimen_count == 2
    assert by_id["atom.llm.chat.v1"].industry_breadth == 2
    assert by_id["atom.llm.chat.v1"].qa_pass == 5  # 3+2 across both sessions
    assert by_id["atom.llm.chat.v1"].qa_fail == 1
    assert by_id["atom.notify.dingtalk.v1"].specimen_count == 1
    assert by_id["atom.db.postgres.v1"].specimen_count == 1


def test_score_cold_start_uses_static_pass_rate(db_path: Path):
    svc = AtomScoreService(
        db_path=db_path,
        atom_static_pass_rates={"atom.unused.v1": 0.85},
    )
    score = svc.score("atom.unused.v1")
    assert score.has_history is False
    assert score.score == 0.85
    assert "cold_start" in score.components or "cold_start_static_pass_rate" in score.components


def test_score_cold_start_unknown_atom_returns_zero(db_path: Path):
    svc = AtomScoreService(db_path=db_path)
    score = svc.score("atom.never.heard.v1")
    assert score.has_history is False
    assert score.score == 0.0


def test_score_combines_invocations_success_and_recency(db_path: Path):
    _add_wiki(db_path, slug="popular", session_id="s1", industry="01",
              atom_ids=["atom.llm.chat.v1"], days_ago=0)
    _add_qa_run(db_path, session_id="s1", verdict="pass", pass_count=10, fail_count=0)

    svc = AtomScoreService(db_path=db_path)
    s = svc.score("atom.llm.chat.v1")

    assert s.has_history is True
    assert s.score > 0.0
    assert s.components["success_rate"] == 1.0
    assert s.components["recency_decay"] > 0.95  # used today -> near 1
    assert s.components["invocations_norm"] > 0.0


def test_all_fail_atom_lowers_score(db_path: Path):
    _add_wiki(db_path, slug="bad", session_id="s1", industry="01",
              atom_ids=["atom.failing.v1"])
    _add_qa_run(db_path, session_id="s1", verdict="fail", pass_count=0, fail_count=5)

    svc = AtomScoreService(db_path=db_path)
    s = svc.score("atom.failing.v1")

    assert s.components["success_rate"] == 0.0
    assert s.score < 0.5  # bounded above by W_INVOCATIONS + W_RECENCY


def test_recency_decay_for_old_atom(db_path: Path):
    _add_wiki(db_path, slug="dusty", session_id="s1", industry="01",
              atom_ids=["atom.llm.chat.v1"], days_ago=120)
    _add_qa_run(db_path, session_id="s1", verdict="pass", pass_count=5, fail_count=0)

    svc = AtomScoreService(db_path=db_path)
    s = svc.score("atom.llm.chat.v1")
    # 120 days at 30-day half-life -> exp(-4) ~= 0.018
    assert s.components["recency_decay"] < 0.05


def test_rank_sorts_high_to_low(db_path: Path):
    _add_wiki(db_path, slug="A", session_id="sA", industry="01",
              atom_ids=["atom.popular.v1"])
    _add_qa_run(db_path, session_id="sA", verdict="pass", pass_count=20, fail_count=0)
    _add_wiki(db_path, slug="B", session_id="sB", industry="02",
              atom_ids=["atom.mediocre.v1"])
    _add_qa_run(db_path, session_id="sB", verdict="partial", pass_count=2, fail_count=3)

    svc = AtomScoreService(db_path=db_path)
    ranked = svc.rank()
    ids = [r.atom_id for r in ranked]
    assert ids.index("atom.popular.v1") < ids.index("atom.mediocre.v1")


def test_rank_filters_by_layer_prefix(db_path: Path):
    _add_wiki(db_path, slug="x", session_id="sx", industry="01",
              atom_ids=["atom.foo.v1", "prompt.bar.v1", "combo.baz.v1"])

    svc = AtomScoreService(db_path=db_path)
    atom_only = [r.atom_id for r in svc.rank(layer_prefix="atom.")]
    assert "atom.foo.v1" in atom_only
    assert "prompt.bar.v1" not in atom_only
    assert "combo.baz.v1" not in atom_only


def test_rank_includes_cold_start_atoms_with_static_pass_rate(db_path: Path):
    _add_wiki(db_path, slug="x", session_id="sx", industry="01",
              atom_ids=["atom.used.v1"])
    _add_qa_run(db_path, session_id="sx", verdict="pass", pass_count=3, fail_count=0)

    svc = AtomScoreService(
        db_path=db_path,
        atom_static_pass_rates={
            "atom.used.v1": 0.99,
            "atom.cold.v1": 0.5,  # never used but registered in atom yaml
        },
    )
    ranked = {r.atom_id: r for r in svc.rank()}
    assert "atom.cold.v1" in ranked
    assert ranked["atom.cold.v1"].has_history is False
    assert ranked["atom.cold.v1"].score == 0.5
    # used atom should rank above cold one
    cold_idx = [r.atom_id for r in svc.rank()].index("atom.cold.v1")
    used_idx = [r.atom_id for r in svc.rank()].index("atom.used.v1")
    assert used_idx < cold_idx


def test_view_sql_ignores_empty_atom_ids_json(db_path: Path):
    _add_wiki(db_path, slug="empty1", session_id="se1", industry="01", atom_ids=[])
    _add_wiki(db_path, slug="real", session_id="sr", industry="01",
              atom_ids=["atom.real.v1"])
    svc = AtomScoreService(db_path=db_path)
    stats = svc.all_stats()
    ids = [s.atom_id for s in stats]
    assert ids == ["atom.real.v1"]
