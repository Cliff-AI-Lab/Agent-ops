"""AtomScoreService - Phase 9 W1 atom usage aggregation + score.

Closes the lights-out factory's reuse-feedback loop:

    atom yaml (static)
        |
        +- deploy/wizard writes wiki_articles.atom_ids_json (V2.5 W6)
        +- qa pipeline writes qa_runs.pass_count/fail_count (V2.5 W8)
        |
        v
    AtomUsageView (this module's SQL) -> per-atom rollup
        |
        v
    AtomScoreService.score(asset_id)
        |
        +- Resolver score signal (Phase 9 W1 Day 3)
        +- harness factory atom-score / atom-rank CLI (Phase 9 W1 Day 2)
        +- harness factory wiki sync --to obsidian (Phase 9 W1 Day 2)

Design choices (per 2026-05-08 R3 reuse audit):
  - Do NOT create a new atom_usage_events table. Reuse:
      * wiki_articles.atom_ids_json   (V2.5 W6 auto-populated on wizard deploy)
      * qa_runs (session_id, verdict, pass_count, fail_count)  (V2.5 W8)
      * factory_sessions (session_id, industry_code)
  - AtomUsageView is a SQL CTE here, not a persisted view, so the schema is
    untouched (zero migration risk on V2.5.x baselines).
  - Score formula A (linear weighted, default-recommended in Phase 9 design
    doc): score = 0.4*log10(1+invocations) + 0.4*success_rate + 0.2*recency.
    Cold-start: when the atom has never been used in a deployed wiki, return
    its static atom yaml metrics.pass_rate (fallback baseline).
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# AtomUsageView - reuse-only SQL, no schema migration. Driver = wiki_articles.
# qa_runs joined by session_id (LEFT JOIN; missing rows mean "no QA recorded").
ATOM_USAGE_VIEW_SQL = """
WITH atom_from_wiki AS (
    SELECT
        je.value          AS atom_id,
        wa.system_slug    AS specimen_slug,
        wa.session_id     AS session_id,
        wa.qa_sign_off    AS wiki_qa_sign_off,
        wa.industry_code  AS industry_code,
        wa.created_at     AS used_at
    FROM wiki_articles wa, json_each(wa.atom_ids_json) je
    WHERE wa.atom_ids_json IS NOT NULL
      AND wa.atom_ids_json != '[]'
)
SELECT
    afw.atom_id                                                     AS atom_id,
    COUNT(DISTINCT afw.specimen_slug)                               AS specimen_count,
    SUM(CASE WHEN afw.wiki_qa_sign_off = 'PASS' THEN 1 ELSE 0 END)  AS wiki_pass,
    SUM(CASE WHEN afw.wiki_qa_sign_off IN ('REDO','REJECT')
             THEN 1 ELSE 0 END)                                     AS wiki_reject,
    COALESCE(SUM(q.pass_count), 0)                                  AS qa_pass,
    COALESCE(SUM(q.fail_count), 0)                                  AS qa_fail,
    COUNT(DISTINCT afw.industry_code)                               AS industry_breadth,
    MAX(afw.used_at)                                                AS last_used_at,
    GROUP_CONCAT(DISTINCT afw.specimen_slug)                        AS specimens
FROM atom_from_wiki afw
LEFT JOIN qa_runs q ON q.session_id = afw.session_id
GROUP BY afw.atom_id
"""


@dataclass
class AtomUsageStats:
    """Per-atom aggregate from AtomUsageView."""

    atom_id: str
    specimen_count: int
    wiki_pass: int
    wiki_reject: int
    qa_pass: int
    qa_fail: int
    industry_breadth: int
    last_used_at: str | None
    specimens: list[str] = field(default_factory=list)

    @property
    def total_invocations(self) -> int:
        # Each (specimen, qa_run) pair counts; specimen_count is the floor.
        # qa_pass+qa_fail captures repeated test runs for the same specimen.
        return max(self.specimen_count, self.qa_pass + self.qa_fail)

    @property
    def success_rate(self) -> float:
        wiki_total = self.wiki_pass + self.wiki_reject
        qa_total = self.qa_pass + self.qa_fail
        if qa_total > 0:
            return self.qa_pass / qa_total
        if wiki_total > 0:
            return self.wiki_pass / wiki_total
        return 0.0

    @property
    def days_since_last_use(self) -> float | None:
        if not self.last_used_at:
            return None
        try:
            t = datetime.fromisoformat(self.last_used_at.replace("Z", "+00:00"))
        except ValueError:
            return None
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - t
        return delta.total_seconds() / 86400.0


@dataclass
class AtomScore:
    """Final per-atom score with explanation."""

    atom_id: str
    score: float
    has_history: bool
    static_pass_rate: float | None
    stats: AtomUsageStats | None
    components: dict[str, float] = field(default_factory=dict)
    explanation: str = ""


class AtomScoreService:
    """Compute per-atom usage score from harness.db reuse data.

    No schema migration. Pure read.
    """

    # Score formula A weights (Phase 9 design doc, recommended default).
    W_INVOCATIONS = 0.4
    W_SUCCESS = 0.4
    W_RECENCY = 0.2
    RECENCY_HALFLIFE_DAYS = 30.0

    def __init__(
        self,
        db_path: Path | str,
        atom_static_pass_rates: dict[str, float] | None = None,
    ):
        self.db_path = Path(db_path)
        self._static_pass = atom_static_pass_rates or {}

    @classmethod
    def from_atom_loader(
        cls,
        db_path: Path | str,
        atoms: dict[str, Any],
    ) -> "AtomScoreService":
        """Build with cold-start fallback drawn from atom yaml metrics."""
        static = {}
        for asset_id, atom in atoms.items():
            metrics = getattr(atom, "metrics", None)
            pass_rate = getattr(metrics, "pass_rate", None) if metrics else None
            if pass_rate is not None:
                static[asset_id] = float(pass_rate)
        return cls(db_path=db_path, atom_static_pass_rates=static)

    # ----- public --------------------------------------------------------

    def all_stats(self) -> list[AtomUsageStats]:
        rows = self._connect().execute(ATOM_USAGE_VIEW_SQL).fetchall()
        return [self._row_to_stats(r) for r in rows]

    def stats_for(self, atom_id: str) -> AtomUsageStats | None:
        for s in self.all_stats():
            if s.atom_id == atom_id:
                return s
        return None

    def score(self, atom_id: str) -> AtomScore:
        stats = self.stats_for(atom_id)
        static = self._static_pass.get(atom_id)
        if stats is None or stats.total_invocations == 0:
            cold = static if static is not None else 0.0
            return AtomScore(
                atom_id=atom_id,
                score=cold,
                has_history=False,
                static_pass_rate=static,
                stats=stats,
                components={"cold_start_static_pass_rate": cold},
                explanation=(
                    f"no usage history; falling back to atom yaml "
                    f"metrics.pass_rate={static!r}"
                ),
            )
        return self._score_from_stats(stats, static)

    def rank(
        self,
        layer_prefix: str = "atom.",
        top: int | None = None,
    ) -> list[AtomScore]:
        seen: set[str] = set()
        out: list[AtomScore] = []
        for s in self.all_stats():
            if not s.atom_id.startswith(layer_prefix):
                continue
            seen.add(s.atom_id)
            out.append(self._score_from_stats(s, self._static_pass.get(s.atom_id)))
        # Add cold-start atoms that have static pass_rate but no history.
        for asset_id, static in self._static_pass.items():
            if asset_id in seen or not asset_id.startswith(layer_prefix):
                continue
            out.append(
                AtomScore(
                    atom_id=asset_id,
                    score=static,
                    has_history=False,
                    static_pass_rate=static,
                    stats=None,
                    components={"cold_start_static_pass_rate": static},
                    explanation="no usage history",
                )
            )
        out.sort(key=lambda x: (x.score, x.has_history), reverse=True)
        if top is not None:
            out = out[:top]
        return out

    # ----- internals -----------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        if not self.db_path.exists():
            raise FileNotFoundError(f"harness.db not found: {self.db_path}")
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _row_to_stats(self, row: sqlite3.Row) -> AtomUsageStats:
        specimens_csv = row["specimens"] or ""
        return AtomUsageStats(
            atom_id=row["atom_id"],
            specimen_count=row["specimen_count"] or 0,
            wiki_pass=row["wiki_pass"] or 0,
            wiki_reject=row["wiki_reject"] or 0,
            qa_pass=row["qa_pass"] or 0,
            qa_fail=row["qa_fail"] or 0,
            industry_breadth=row["industry_breadth"] or 0,
            last_used_at=row["last_used_at"],
            specimens=[s for s in specimens_csv.split(",") if s],
        )

    def _score_from_stats(
        self, stats: AtomUsageStats, static_pass: float | None
    ) -> AtomScore:
        invocations_norm = math.log10(1 + stats.total_invocations) / math.log10(1 + 100)
        invocations_norm = min(1.0, invocations_norm)

        success = stats.success_rate
        days = stats.days_since_last_use
        recency = math.exp(-days / self.RECENCY_HALFLIFE_DAYS) if days is not None else 0.0

        raw = (
            self.W_INVOCATIONS * invocations_norm
            + self.W_SUCCESS * success
            + self.W_RECENCY * recency
        )
        return AtomScore(
            atom_id=stats.atom_id,
            score=round(raw, 4),
            has_history=True,
            static_pass_rate=static_pass,
            stats=stats,
            components={
                "invocations_norm": round(invocations_norm, 4),
                "success_rate": round(success, 4),
                "recency_decay": round(recency, 4),
                "weights": f"w_inv={self.W_INVOCATIONS} w_succ={self.W_SUCCESS} w_rec={self.W_RECENCY}",
            },
            explanation=(
                f"used in {stats.specimen_count} specimens, "
                f"qa pass/fail = {stats.qa_pass}/{stats.qa_fail}, "
                f"last used {f'{days:.1f}d ago' if days is not None else 'unknown'}, "
                f"industry breadth = {stats.industry_breadth}"
            ),
        )
