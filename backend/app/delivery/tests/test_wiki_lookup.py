"""Phase 10 W1 Day 1 - tests for WikiLookup."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.delivery.wiki_lookup import WikiHit, WikiLookup, jaccard, tokenize


def _seed_schema(path: Path) -> None:
    sqlite3.connect(path).executescript(
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
        )
        """
    )


def _add(
    path: Path,
    *,
    slug: str,
    title: str,
    summary: str,
    industry: str = "01",
    scenarios: list[str] | None = None,
    fits: list[str] | None = None,
    atoms: list[str] | None = None,
    qa: str = "PASS",
) -> None:
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn = sqlite3.connect(path)
    conn.execute(
        """INSERT INTO wiki_articles (
            system_slug, session_id, industry_code, title, summary, body_md,
            scenarios_json, fits_json, atom_ids_json, qa_sign_off,
            created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            slug, f"sess-{slug}", industry, title, summary, "body",
            json.dumps(scenarios or []),
            json.dumps(fits or []),
            json.dumps(atoms or []),
            qa, ts, ts,
        ),
    )
    conn.commit()
    conn.close()


@pytest.fixture
def db(tmp_path: Path) -> Path:
    p = tmp_path / "harness.db"
    _seed_schema(p)
    return p


# ----- tokenize ------------------------------------------------------


def test_tokenize_english_basic():
    assert tokenize("Daily Report") == {"daily", "report"}


def test_tokenize_drops_stopwords():
    assert tokenize("the report of the day") == {"report", "day"}


def test_tokenize_chinese_per_char():
    out = tokenize("智能日报")
    assert out == {"智", "能", "日", "报"}


def test_tokenize_mixed():
    out = tokenize("生成 weekly report 给运营")
    assert "weekly" in out
    assert "report" in out
    assert "生" in out
    assert "成" in out
    assert "运" in out
    assert "营" in out
    assert "给" not in out  # stopword


def test_tokenize_punctuation_split():
    out = tokenize("hello, world; 你好,世界!")
    assert "hello" in out and "world" in out
    assert "你" in out and "好" in out and "世" in out and "界" in out


def test_tokenize_empty_returns_empty_set():
    assert tokenize("") == set()
    assert tokenize("   ") == set()


# ----- jaccard -------------------------------------------------------


def test_jaccard_identical():
    s = {"a", "b", "c"}
    assert jaccard(s, s) == 1.0


def test_jaccard_disjoint():
    assert jaccard({"a"}, {"b"}) == 0.0


def test_jaccard_partial():
    # |∩| = 1, |∪| = 3
    assert jaccard({"a", "b"}, {"b", "c"}) == pytest.approx(1 / 3)


def test_jaccard_empty():
    assert jaccard(set(), set()) == 0.0
    assert jaccard({"a"}, set()) == 0.0


# ----- find_similar -------------------------------------------------


def test_find_similar_empty_db_returns_empty(db: Path):
    lk = WikiLookup(db)
    assert lk.find_similar("查 sales 出周报") == []


def test_find_similar_finds_matching_row(db: Path):
    _add(
        db,
        slug="weekly-sales",
        title="销售周报智能体",
        summary="每周一查 sales_db 自动出周报",
        scenarios=["销售跟踪", "周度汇报"],
        atoms=["atom.db.postgres.v1", "atom.llm.chat.v1"],
    )
    lk = WikiLookup(db, threshold=0.05)
    hits = lk.find_similar("查 sales 出周报")
    assert len(hits) == 1
    assert hits[0].system_slug == "weekly-sales"
    assert hits[0].similarity > 0.05
    assert "atom.db.postgres.v1" in hits[0].atom_ids


def test_find_similar_top_k_truncation(db: Path):
    for i in range(5):
        _add(db, slug=f"s{i}", title=f"销售周报 {i}", summary="sales report")
    lk = WikiLookup(db, threshold=0.0)
    hits = lk.find_similar("销售周报", top_k=3)
    assert len(hits) == 3


def test_find_similar_threshold_filters(db: Path):
    _add(db, slug="unrelated", title="天气预报", summary="weather forecast")
    _add(db, slug="related", title="销售周报", summary="sales report")
    lk = WikiLookup(db, threshold=0.20)
    hits = lk.find_similar("销售周报")
    assert len(hits) == 1
    assert hits[0].system_slug == "related"


def test_find_similar_sorted_descending(db: Path):
    _add(db, slug="exact", title="销售日报销售日报", summary="sales daily report")
    _add(db, slug="loose", title="制造业故障诊断", summary="manufacturing diagnosis")
    lk = WikiLookup(db, threshold=0.0)
    hits = lk.find_similar("销售日报")
    # exact should outrank loose
    assert hits[0].system_slug == "exact"
    assert hits[0].similarity >= hits[-1].similarity


def test_find_similar_industry_filter(db: Path):
    _add(db, slug="bank", title="银行客服", summary="客户服务", industry="06")
    _add(db, slug="general", title="客服系统", summary="客户服务", industry="01")
    lk = WikiLookup(db, threshold=0.0)
    bank_only = lk.find_similar("客服", industry_filter="06")
    assert len(bank_only) == 1
    assert bank_only[0].system_slug == "bank"


def test_find_similar_returns_matched_terms(db: Path):
    _add(db, slug="x", title="销售日报", summary="report")
    lk = WikiLookup(db, threshold=0.0)
    hits = lk.find_similar("销售日报 generation")
    assert hits
    assert "销" in hits[0].matched_terms
    assert "日" in hits[0].matched_terms


def test_find_similar_empty_nl_returns_empty(db: Path):
    _add(db, slug="x", title="anything", summary="anything")
    lk = WikiLookup(db)
    assert lk.find_similar("") == []


def test_find_similar_handles_missing_atom_ids_gracefully(db: Path):
    _add(db, slug="bare", title="销售周报", summary="report", atoms=[])
    lk = WikiLookup(db, threshold=0.0)
    hits = lk.find_similar("销售周报")
    assert hits[0].atom_ids == []


def test_wiki_hit_short_line_format():
    h = WikiHit(
        system_slug="x",
        title="a long-ish title",
        summary="...",
        similarity=0.42,
        industry_code="01",
        qa_sign_off="PASS",
    )
    line = h.short_line()
    assert "sim=0.420" in line
    assert "ind=01" in line
    assert "qa=PASS" in line
    assert "slug=x" in line


def test_db_missing_raises():
    lk = WikiLookup(Path("/no/such/db.sqlite"))
    with pytest.raises(FileNotFoundError):
        lk.find_similar("anything")
