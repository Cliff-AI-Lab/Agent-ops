"""WikiLookup - Phase 10 W1 Day 1 NL similarity search over wiki_articles.

The first stage of Phase 10's two-path pipeline:

    NL ──► WikiLookup.find_similar(nl, top_k=3) ──► WikiHit[]
                                                     │
                                                     +─► hit  → suggest reuse / clone
                                                     +─► miss → continue to build

R3 reuse audit:
  - wiki_articles table populated by V2.5 W6 auto_register on every wizard
    deploy. We only READ it; no schema migration.
  - api/wiki.py already exposes list/search/get/markdown - but its `search`
    is a name-prefix grep, not similarity. We supply the similarity layer.

Algorithm V0 (Jaccard on tokenized text):
  - tokenize(text): English by whitespace + lower; CJK by per-character
    grams (no jieba dependency). Stop-words list is bilingual.
  - Compare NL tokens against each row's
    (title + summary + scenarios_json values + fits_json values).
  - Score = |A ∩ B| / |A ∪ B|. Threshold 0.10 by default.
  - V1 (Phase 10 W2) will swap in embeddings; the WikiHit dataclass and
    find_similar signature stay the same so callers don't churn.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


_STOPWORDS: set[str] = {
    # English
    "the", "a", "an", "and", "or", "of", "in", "on", "at", "to", "for",
    "with", "by", "from", "as", "is", "are", "was", "were", "be", "been",
    "being", "this", "that", "these", "those", "it", "its", "i", "me", "my",
    "we", "us", "our", "you", "your", "he", "she", "they", "them", "their",
    # 中文常见虚词(单字) - 不含人称代词,因为它们常是搜索关键词的一部分
    "的", "了", "和", "是", "有", "在", "上", "下", "到", "从", "把", "对",
    "为", "也", "就", "都", "之", "及", "这", "那", "可", "再", "用", "给",
    "向", "或", "因", "所", "以", "与", "等", "啊", "吧", "呢", "嘛", "哦",
    # 标点 / 单字符 noise
    " ", "", ".", ",", ";", ":", "(", ")", "[", "]",
}

_TOKEN_SPLIT = re.compile(
    r"[\s.,;:!?。,;:!?、""''「」『』《》【】()\[\]{}<>—_/\\|+*=#&^%$@~`'\"-]+"
)


def tokenize(text: str) -> set[str]:
    """V0 tokenizer: ASCII words by whitespace, CJK by per-character.

    Returns a set so Jaccard math is straightforward. Stop-words filtered.
    """
    if not text:
        return set()
    out: set[str] = set()
    for chunk in _TOKEN_SPLIT.split(text):
        if not chunk:
            continue
        if chunk.isascii():
            tok = chunk.lower().strip()
            if tok and tok not in _STOPWORDS:
                out.add(tok)
            continue
        # mixed or pure CJK chunk - split per char
        for ch in chunk:
            if ch in _STOPWORDS:
                continue
            cp = ord(ch)
            # CJK + extensions
            if (
                0x4E00 <= cp <= 0x9FFF
                or 0x3400 <= cp <= 0x4DBF
                or 0xF900 <= cp <= 0xFAFF
            ):
                out.add(ch)
            elif ch.isascii() and ch.isalnum():
                out.add(ch.lower())
    return out


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


@dataclass
class WikiHit:
    system_slug: str
    title: str
    summary: str
    similarity: float
    industry_code: str | None = None
    atom_ids: list[str] = field(default_factory=list)
    qa_sign_off: str | None = None
    created_at: str | None = None
    matched_terms: list[str] = field(default_factory=list)

    def short_line(self) -> str:
        ind = self.industry_code or "-"
        qa = self.qa_sign_off or "-"
        return (
            f"sim={self.similarity:.3f} ind={ind} qa={qa} "
            f"slug={self.system_slug} title={self.title[:40]}"
        )


class WikiLookup:
    """Read-only similarity search over wiki_articles.

    Args:
        db_path: harness.db (V2.5 schema; reads wiki_articles only).
        threshold: minimum similarity to be returned (default 0.10).
    """

    DEFAULT_THRESHOLD = 0.10

    def __init__(
        self,
        db_path: Path | str,
        threshold: float = DEFAULT_THRESHOLD,
    ):
        self.db_path = Path(db_path)
        self.threshold = threshold

    def find_similar(
        self,
        nl: str,
        top_k: int = 3,
        industry_filter: str | None = None,
    ) -> list[WikiHit]:
        """Return top-k wiki articles by Jaccard similarity to nl.

        Args:
            nl: natural language input (the user's specimen brief).
            top_k: max number of hits to return.
            industry_filter: if given, restrict to wiki rows with that
                industry_code (e.g. "01" for general business).

        Returns:
            Sorted (highest similarity first) list of WikiHit. Empty when
            no row clears the threshold or db is empty.
        """
        nl_tokens = tokenize(nl)
        if not nl_tokens:
            return []

        rows = self._load_rows(industry_filter)
        scored: list[WikiHit] = []
        for row in rows:
            haystack = self._row_text(row)
            row_tokens = tokenize(haystack)
            if not row_tokens:
                continue
            score = jaccard(nl_tokens, row_tokens)
            if score < self.threshold:
                continue
            scored.append(
                WikiHit(
                    system_slug=row["system_slug"],
                    title=row["title"],
                    summary=row["summary"],
                    similarity=round(score, 4),
                    industry_code=row["industry_code"],
                    atom_ids=self._parse_json_list(row["atom_ids_json"]),
                    qa_sign_off=row["qa_sign_off"],
                    created_at=row["created_at"],
                    matched_terms=sorted(nl_tokens & row_tokens),
                )
            )
        scored.sort(key=lambda h: h.similarity, reverse=True)
        return scored[:top_k]

    # ----- internals -----------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        if not self.db_path.exists():
            raise FileNotFoundError(f"harness.db not found: {self.db_path}")
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _load_rows(self, industry: str | None) -> list[sqlite3.Row]:
        conn = self._connect()
        if industry:
            rows = conn.execute(
                """SELECT system_slug, title, summary, scenarios_json,
                          fits_json, atom_ids_json, industry_code,
                          qa_sign_off, created_at
                   FROM wiki_articles WHERE industry_code = ?
                """,
                (industry,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT system_slug, title, summary, scenarios_json,
                          fits_json, atom_ids_json, industry_code,
                          qa_sign_off, created_at
                   FROM wiki_articles
                """
            ).fetchall()
        conn.close()
        return rows

    @staticmethod
    def _row_text(row: sqlite3.Row) -> str:
        parts: list[str] = [row["title"] or "", row["summary"] or ""]
        for key in ("scenarios_json", "fits_json"):
            try:
                items = json.loads(row[key] or "[]")
                if isinstance(items, list):
                    parts.extend(str(s) for s in items if s)
            except json.JSONDecodeError:
                pass
        return " ".join(parts)

    @staticmethod
    def _parse_json_list(s: Any) -> list[str]:
        if not s:
            return []
        try:
            v = json.loads(s)
            if isinstance(v, list):
                return [str(x) for x in v]
        except (json.JSONDecodeError, TypeError):
            pass
        return []
