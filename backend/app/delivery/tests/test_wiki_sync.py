"""Phase 9 W1 Day 2 - WikiSync tests.

Verifies:
1. Empty db -> summary is rendered with placeholder, atoms/ folder empty.
2. Real db with one wiki -> summary contains atom rank, detail .md per atom.
3. Re-run is idempotent (overwrites, no leftover stale files for atoms still
   present; though we don't yet prune removed atoms — that's W2).
4. No emoji in any rendered output (project hard rule).
5. Cold-start atoms with static pass_rate appear with "cold" marker.
6. Per-atom detail page lists specimens that referenced it.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.delivery.atom_score_service import AtomScoreService
from app.delivery.wiki_sync import WikiSync


def _seed_db(path: Path) -> None:
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


def _add_wiki(path: Path, slug, session_id, industry, atoms, qa="PASS", title=None) -> None:
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn = sqlite3.connect(path)
    conn.execute(
        """INSERT INTO wiki_articles (
            system_slug, session_id, industry_code, title, summary, body_md,
            scenarios_json, fits_json, atom_ids_json, qa_sign_off,
            created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (slug, session_id, industry, title or slug, "summary", "body",
         "[]", "[]", json.dumps(atoms), qa, ts, ts),
    )
    conn.commit()
    conn.close()


def _add_qa(path: Path, session_id, pass_count=1, fail_count=0) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """INSERT INTO qa_runs (session_id, stage, agent_role, verdict,
            pass_count, fail_count, cases_json, report_md, created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (session_id, "uat", "tester", "pass", pass_count, fail_count,
         "[]", "report",
         datetime.now(timezone.utc).isoformat(timespec="seconds")),
    )
    conn.commit()
    conn.close()


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "harness.db"
    _seed_db(p)
    return p


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    v = tmp_path / "asset_center"
    v.mkdir()
    return v


def _no_emoji(text: str) -> bool:
    """Crude emoji guard: rejects any char in BMP supplementary planes used
    by emoji + the misc symbols/pictographs blocks."""
    for ch in text:
        cp = ord(ch)
        if 0x1F000 <= cp <= 0x1FBFF:  # emoji + pictograph supplementary
            return False
        if 0x2600 <= cp <= 0x27BF:    # misc symbols + dingbats
            return False
    return True


def test_empty_db_renders_placeholder(db_path: Path, vault: Path):
    svc = AtomScoreService(db_path=db_path)
    sync = WikiSync(svc)
    result = sync.sync_obsidian(vault)

    assert result.summary_path.exists()
    assert result.atoms_rendered == 0
    txt = result.summary_path.read_text(encoding="utf-8")
    assert "L2 原子能力 · 使用度自动渲染" in txt
    assert "尚无可聚合" in txt
    assert _no_emoji(txt), "rendered output must not contain emoji"

    detail_dir = vault / "atoms"
    assert detail_dir.exists()
    assert list(detail_dir.glob("L2-atom-*.md")) == []


def test_real_data_renders_rank_table(db_path: Path, vault: Path):
    _add_wiki(db_path, "banking", "s1", "01",
              ["atom.llm.chat.v1", "atom.notify.dingtalk.v1"], title="Bank QA")
    _add_qa(db_path, "s1", pass_count=3, fail_count=0)

    svc = AtomScoreService(db_path=db_path)
    sync = WikiSync(svc)
    result = sync.sync_obsidian(vault)

    assert result.atoms_rendered >= 2
    summary = result.summary_path.read_text(encoding="utf-8")
    assert "atom.llm.chat.v1" in summary
    assert "atom.notify.dingtalk.v1" in summary
    assert "0.4 · log10" in summary  # formula explanation present
    assert _no_emoji(summary)

    llm_detail = vault / "atoms" / "L2-atom-atom.llm.chat.v1.md"
    assert llm_detail.exists()
    detail_txt = llm_detail.read_text(encoding="utf-8")
    assert "atom.llm.chat.v1" in detail_txt
    assert "Bank QA" in detail_txt or "banking" in detail_txt
    assert _no_emoji(detail_txt)


def test_cold_start_atom_marked(db_path: Path, vault: Path):
    svc = AtomScoreService(
        db_path=db_path,
        atom_static_pass_rates={"atom.unused.v1": 0.7},
    )
    sync = WikiSync(svc)
    result = sync.sync_obsidian(vault)

    summary = result.summary_path.read_text(encoding="utf-8")
    assert "atom.unused.v1" in summary
    assert "cold" in summary.lower()  # marker present


def test_resync_is_idempotent(db_path: Path, vault: Path):
    _add_wiki(db_path, "x", "sx", "01", ["atom.foo.v1"])
    _add_qa(db_path, "sx", pass_count=1)

    svc = AtomScoreService(db_path=db_path)
    sync = WikiSync(svc)

    r1 = sync.sync_obsidian(vault)
    body1 = r1.summary_path.read_text(encoding="utf-8")
    detail1 = (vault / "atoms" / "L2-atom-atom.foo.v1.md").read_text(encoding="utf-8")

    r2 = sync.sync_obsidian(vault)
    body2 = r2.summary_path.read_text(encoding="utf-8")
    detail2 = (vault / "atoms" / "L2-atom-atom.foo.v1.md").read_text(encoding="utf-8")

    # Banner has a rendered_at timestamp that may differ; strip date lines.
    def _strip_dates(s):
        return "\n".join(
            line for line in s.split("\n")
            if not line.startswith("> 上次渲染")
        )

    assert _strip_dates(body1) == _strip_dates(body2)
    assert _strip_dates(detail1) == _strip_dates(detail2)


def test_per_atom_detail_lists_specimens(db_path: Path, vault: Path):
    _add_wiki(db_path, "spec_a", "sa", "01", ["atom.shared.v1"], title="Spec A")
    _add_wiki(db_path, "spec_b", "sb", "05", ["atom.shared.v1"], title="Spec B")
    _add_qa(db_path, "sa", pass_count=2)
    _add_qa(db_path, "sb", pass_count=1)

    svc = AtomScoreService(db_path=db_path)
    sync = WikiSync(svc)
    sync.sync_obsidian(vault)

    detail = (vault / "atoms" / "L2-atom-atom.shared.v1.md").read_text(encoding="utf-8")
    assert "Spec A" in detail
    assert "Spec B" in detail
    assert "01" in detail and "05" in detail  # industries listed


def test_summary_contains_wikilinks_to_detail(db_path: Path, vault: Path):
    _add_wiki(db_path, "x", "sx", "01", ["atom.bar.v1"])
    _add_qa(db_path, "sx", pass_count=1)

    svc = AtomScoreService(db_path=db_path)
    sync = WikiSync(svc)
    sync.sync_obsidian(vault)

    summary = (vault / "L2-原子能力-自动渲染.md").read_text(encoding="utf-8")
    assert "[[atoms/L2-atom-atom.bar.v1" in summary
