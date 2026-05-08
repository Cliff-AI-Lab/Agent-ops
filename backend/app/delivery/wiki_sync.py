"""WikiSync - render harness.db reuse data into Obsidian-friendly markdown.

Phase 9 W1 Day 2. Closes the visual side of the reuse loop:

    harness.db wiki_articles + qa_runs
        |
        v
    AtomScoreService.rank()                         (Phase 9 W1 Day 1)
        |
        v
    WikiSync.sync_obsidian(vault_path)              (this module)
        |
        +--> 资产中心/L2-原子能力-自动渲染.md         (rank table)
        +--> 资产中心/atoms/L2-atom-<asset_id>.md    (one detail per atom)

Design constraints (per project hard rules):
  - NO emoji in any rendered output. Almanac aesthetic only.
  - Obsidian wikilink + tags for graph navigability.
  - The hand-written 资产中心/L2-原子能力.md is NOT touched. The auto file
    has '-自动渲染' suffix so the user can keep the curated version.
  - Re-running sync is idempotent (overwrites, no duplicate inserts).
  - Tag scheme: #asset/atom + #asset/atom-detail (filterable in graph view).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.delivery.atom_score_service import AtomScore, AtomScoreService


@dataclass
class _AtomWikiContext:
    """Per-atom context: specimens that used it, what scenarios they served."""

    atom_id: str
    specimens: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class WikiSyncResult:
    summary_path: Path
    detail_paths: list[Path]
    rendered_at: str
    atoms_rendered: int


class WikiSync:
    """Render harness.db reuse data into Obsidian markdown.

    Args:
        score_service: source of per-atom scores (Phase 9 W1 Day 1).
        db_path: harness.db (used to fetch per-specimen wiki context).
    """

    AUTO_FILE_BANNER = (
        "> 此文件由 `harness factory wiki sync --to obsidian` 自动重写。\n"
        "> 数据来源:harness.db `wiki_articles` + `qa_runs` + atom yaml `metrics`。\n"
        "> 编辑请改源(atom yaml / 重新 deploy / 重跑 QA),不要直接编辑此文件。\n"
    )

    def __init__(self, score_service: AtomScoreService, db_path: Path | str | None = None):
        self.scores = score_service
        self.db_path = Path(db_path) if db_path else score_service.db_path

    # ----- public --------------------------------------------------------

    def sync_obsidian(
        self,
        vault_path: Path | str,
        layer_prefix: str = "atom.",
        top: int | None = None,
    ) -> WikiSyncResult:
        """Render the rank summary + per-atom detail pages into the vault.

        vault_path is the asset-center root (e.g. ``资产中心/``). The summary
        lands directly under it; per-atom details go into an ``atoms/``
        subfolder so the manually curated index stays untouched.
        """
        vault = Path(vault_path)
        vault.mkdir(parents=True, exist_ok=True)
        atoms_dir = vault / "atoms"
        atoms_dir.mkdir(exist_ok=True)

        ranked = self.scores.rank(layer_prefix=layer_prefix, top=top)
        contexts = self._fetch_contexts({r.atom_id for r in ranked if r.has_history})

        rendered_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

        summary_path = vault / "L2-原子能力-自动渲染.md"
        summary_path.write_text(
            self._render_summary(ranked, rendered_at, layer_prefix),
            encoding="utf-8",
        )

        detail_paths: list[Path] = []
        for score in ranked:
            ctx = contexts.get(score.atom_id, _AtomWikiContext(atom_id=score.atom_id))
            md = self._render_detail(score, ctx, rendered_at)
            p = atoms_dir / f"L2-atom-{score.atom_id}.md"
            p.write_text(md, encoding="utf-8")
            detail_paths.append(p)

        return WikiSyncResult(
            summary_path=summary_path,
            detail_paths=detail_paths,
            rendered_at=rendered_at,
            atoms_rendered=len(ranked),
        )

    # ----- rendering -----------------------------------------------------

    def _render_summary(
        self, ranked: list[AtomScore], rendered_at: str, layer_prefix: str
    ) -> str:
        out: list[str] = []
        out.append(f"# L2 原子能力 · 使用度自动渲染")
        out.append("")
        out.append("#asset/atom #phase/9 #auto-rendered")
        out.append("")
        out.append(self.AUTO_FILE_BANNER.rstrip())
        out.append(f"> 上次渲染: {rendered_at}  ·  layer: `{layer_prefix}`")
        out.append("")
        out.append("---")
        out.append("")
        out.append("## 排行榜(score 高到低)")
        out.append("")
        if not ranked:
            out.append("> 当前 db 内尚无可聚合的 atom 调用历史。")
            out.append("> 跑一次 `harness factory deploy` 后回来。")
            out.append("")
        else:
            out.append("| 排名 | score | 历史 | specimens | qa pass/fail | 行业 | 最后调用 | 原子 |")
            out.append("|---:|---:|:---:|---:|:---:|---:|---|---|")
            for i, r in enumerate(ranked, 1):
                hist = "yes" if r.has_history else "cold"
                spc = r.stats.specimen_count if r.stats else 0
                qa = (
                    f"{r.stats.qa_pass}/{r.stats.qa_fail}"
                    if r.stats else "-/-"
                )
                inds = r.stats.industry_breadth if r.stats else 0
                last = self._format_last_used(r)
                link = f"[[atoms/L2-atom-{r.atom_id}\\|{r.atom_id}]]"
                out.append(
                    f"| {i} | {r.score:.4f} | {hist} | {spc} | {qa} | {inds} | {last} | {link} |"
                )
            out.append("")

        out.append("---")
        out.append("")
        out.append("## 评分公式")
        out.append("")
        out.append("```")
        out.append("score = 0.4 · log10(1 + invocations)")
        out.append("      + 0.4 · success_rate")
        out.append("      + 0.2 · exp(-days_since_last_use / 30)")
        out.append("```")
        out.append("")
        out.append(
            "冷启(0 调用)atom 退化为其 atom yaml `metrics.pass_rate` 静态值。"
        )
        out.append("")
        out.append("---")
        out.append("")
        out.append("## 链接")
        out.append("")
        out.append("- 手写索引: [[L2-原子能力]](本文件不覆盖)")
        out.append("- 决策: [[决策记录]] §\"Phase 9 提议\"")
        out.append("- 架构图: [[Phase-9-架构图]]")
        out.append("- 主任务: [[Phase-9-资产中枢与自反馈]]")
        out.append("")
        return "\n".join(out)

    def _render_detail(
        self, score: AtomScore, ctx: _AtomWikiContext, rendered_at: str
    ) -> str:
        out: list[str] = []
        out.append(f"# {score.atom_id}")
        out.append("")
        out.append("#asset/atom-detail #phase/9 #auto-rendered")
        out.append("")
        out.append(self.AUTO_FILE_BANNER.rstrip())
        out.append(f"> 上次渲染: {rendered_at}")
        out.append("")
        out.append("---")
        out.append("")

        out.append("## 使用度评分")
        out.append("")
        out.append(f"- **score**: `{score.score:.4f}`")
        out.append(f"- **历史**: {'有' if score.has_history else '冷启(无 deploy 历史)'}")
        if score.static_pass_rate is not None:
            out.append(f"- **atom yaml 静态 pass_rate**: `{score.static_pass_rate}`")
        out.append("")
        if score.components:
            out.append("### 评分构成")
            out.append("")
            for k, v in score.components.items():
                out.append(f"- `{k}`: {v}")
            out.append("")
        if score.explanation:
            out.append(f"> {score.explanation}")
            out.append("")

        out.append("---")
        out.append("")
        out.append("## 实际调用")
        out.append("")
        if score.stats:
            s = score.stats
            out.append(f"- **specimens**: {s.specimen_count}")
            out.append(f"- **qa pass/fail**: {s.qa_pass} / {s.qa_fail}")
            out.append(f"- **industry breadth**: {s.industry_breadth}")
            out.append(f"- **last used**: {s.last_used_at or '-'}")
            out.append("")
            if ctx.specimens:
                out.append("### 被以下 specimen 调用")
                out.append("")
                out.append("| specimen | industry | qa | created_at |")
                out.append("|---|---|---|---|")
                for sp in ctx.specimens:
                    out.append(
                        f"| {sp.get('title') or sp['system_slug']} "
                        f"| {sp.get('industry_code') or '-'} "
                        f"| {sp.get('qa_sign_off') or '-'} "
                        f"| {sp.get('created_at') or '-'} |"
                    )
                out.append("")
        else:
            out.append("> 尚未在任何已发布 wiki 中出现。")
            out.append("> 这个 atom 一被 deploy 调用,这里立刻就有数据。")
            out.append("")

        out.append("---")
        out.append("")
        out.append("## 链接")
        out.append("")
        out.append("- 排行榜: [[L2-原子能力-自动渲染]]")
        out.append(f"- 静态定义: `capabilities/atom/.../{score.atom_id.split('.')[1]}.v1.yaml`")
        out.append("- 决策: [[决策记录]] §\"Phase 9 提议\"")
        out.append("")
        return "\n".join(out)

    # ----- helpers -------------------------------------------------------

    def _format_last_used(self, score: AtomScore) -> str:
        if score.stats and score.stats.last_used_at:
            try:
                return score.stats.last_used_at.split("T")[0]
            except Exception:
                return score.stats.last_used_at
        return "-"

    def _fetch_contexts(self, atom_ids: set[str]) -> dict[str, _AtomWikiContext]:
        """For each atom, look up which wiki articles referenced it."""
        if not atom_ids or not self.db_path.exists():
            return {}
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        rows = cur.execute(
            """
            SELECT system_slug, session_id, industry_code, title,
                   atom_ids_json, qa_sign_off, created_at
            FROM wiki_articles
            WHERE atom_ids_json IS NOT NULL AND atom_ids_json != '[]'
            ORDER BY created_at DESC
            """
        ).fetchall()
        conn.close()

        out: dict[str, _AtomWikiContext] = {a: _AtomWikiContext(atom_id=a) for a in atom_ids}
        for row in rows:
            try:
                atoms = json.loads(row["atom_ids_json"] or "[]")
            except json.JSONDecodeError:
                continue
            for a in atoms:
                if a in out:
                    out[a].specimens.append({
                        "system_slug": row["system_slug"],
                        "session_id": row["session_id"],
                        "industry_code": row["industry_code"],
                        "title": row["title"],
                        "qa_sign_off": row["qa_sign_off"],
                        "created_at": row["created_at"],
                    })
        return out
