"""Rank submodule: LLM-based candidate selection with NOT_applicable as hard constraint.

LLM gets candidates + each candidate's NOT_applicable list as a HARD prompt
constraint (not a soft scoring hint). Output: single selected atom + confidence + reason.

For LLM-subcategory atoms, also outputs llm_task_type + llm_size.

Phase 9 W1 Day 3: optional ``score_service`` injects atom-usage feedback as a
weighted confidence multiplier:

    final = base_confidence * (1 + SCORE_SIGNAL_ALPHA * usage_score)

Cold-start atoms (no usage history) carry score=0, so the signal is a no-op
on first use - the LLM rank decision passes through unchanged. Once an atom
accrues real reuse, popular+reliable parts pull confidence up. Failed atoms
sink. The factory curates its own library.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from pydantic import BaseModel

from app.core.llm.client import ChatMessage, LLMClient
from app.core.pipelines.factory.ir import StepSpec
from app.core.trace.bus import emit
from app.delivery.atom_score_service import AtomScoreService
from app.registry.atom_loader import AtomDef


SCORE_SIGNAL_ALPHA = 0.2  # max +20% lift when a part has perfect usage history


class RankResult(BaseModel):
    asset_id: str
    confidence: float
    reason: str
    llm_task_type: str | None = None
    llm_size: str | None = None


@dataclass
class _Selection:
    atom: AtomDef
    confidence: float
    reason: str
    llm_task_type: str | None = None
    llm_size: str | None = None


_RANK_SYSTEM = """你是 IntentParser 后置的 Resolver Rank 模块。
输入：一个抽象 step（含 verb、constraints、expected_output_kind）+ N 个候选原子（含 description、tags、NOT_applicable）。
任务：从候选里**选 1 个最合适**的，返回 JSON：

{
  "asset_id": "<选中的>",
  "confidence": 0.0-1.0,
  "reason": "<一句话说明为何选它而非其他>",
  "llm_task_type": "<仅当原子 subcategory=LLM 时填，否则 null>",
  "llm_size": "<小|中|大，仅当 LLM 时填，否则 null>"
}

铁律：
- 任何候选的 NOT_applicable 列表里若覆盖到当前 step 场景，**绝对不选**它（硬约束）
- 选 LLM 类原子时，根据 step.verb / constraints 判断 task_type 和 size
  - verb 含 '抽取/提取/分类/标签' → task_type='结构化抽取', size='中'
  - verb 含 '分析/写/生成报告' → task_type='长文档分块总结', size='中'
  - verb 含 '通用对话/问答' → task_type='通用对话', size='中'
  - verb 含 '推理/规划/方案' → task_type='长上下文 / 深度推理', size='大'
- 输出**纯 JSON**，不要 markdown 围栏
"""


async def rank_candidates(
    step: StepSpec,
    candidates: list[tuple[AtomDef, float]],
    llm_client: LLMClient,
    model: str,
    score_service: AtomScoreService | None = None,
) -> _Selection:
    """LLM ranks candidates, returns top-1 with reason.

    If ``score_service`` is provided, a Phase 9 reuse-feedback signal lifts
    the confidence of atoms with strong real-world usage history.
    """
    if not candidates:
        raise ValueError(f"rank_candidates: no candidates for step {step.id}")

    if len(candidates) == 1:
        atom, score = candidates[0]
        # 单候选：subcategory 已硬过滤，唯一项即"必选"，给高基础线 0.75
        # recall 分高时进一步提升，最高 0.95
        sel = _Selection(
            atom=atom,
            confidence=min(0.95, max(0.75, score + 0.6)),
            reason=f"唯一候选（subcategory 过滤后）；recall 分 {score:.2f}",
        )
        if atom.subcategory == "LLM":
            sel.llm_task_type, sel.llm_size = _heuristic_llm_routing(step.verb)
        return _apply_score_signal(sel, step, score_service)

    prompt_user = _build_user_prompt(step, candidates)
    messages = [
        ChatMessage(role="system", content=_RANK_SYSTEM),
        ChatMessage(role="user", content=prompt_user),
    ]
    response = await llm_client.chat(
        model=model, messages=messages, temperature=0.1, max_tokens=400
    )
    raw = response.content.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    try:
        data = json.loads(raw)
        result = RankResult.model_validate(data)
    except Exception as exc:
        emit("L3", "Resolver.rank", "parse_fail", f"step={step.id} err={exc}")
        atom, score = candidates[0]
        sel = _Selection(
            atom=atom,
            confidence=score,
            reason=f"LLM rank parse failed ({exc}); fell back to top recall",
        )
        if atom.subcategory == "LLM":
            sel.llm_task_type, sel.llm_size = _heuristic_llm_routing(step.verb)
        return _apply_score_signal(sel, step, score_service)

    by_id = {a.asset_id: a for a, _ in candidates}
    if result.asset_id not in by_id:
        atom, score = candidates[0]
        sel = _Selection(
            atom=atom,
            confidence=score * 0.5,
            reason=f"LLM picked unknown asset {result.asset_id}; fell back to top recall",
        )
        return _apply_score_signal(sel, step, score_service)
    sel = _Selection(
        atom=by_id[result.asset_id],
        confidence=result.confidence,
        reason=result.reason,
        llm_task_type=result.llm_task_type,
        llm_size=result.llm_size,
    )
    return _apply_score_signal(sel, step, score_service)


def _apply_score_signal(
    selection: _Selection,
    step: StepSpec,
    score_service: AtomScoreService | None,
) -> _Selection:
    """Phase 9 W1 Day 3: weight confidence by atom usage feedback.

    No-op when score_service is None (Phase 8 baseline behavior preserved).
    Always emits a trace event when the service is present, even on cold-
    start atoms (score=0), so observers see signals consistently.
    """
    if score_service is None:
        return selection
    asset_id = selection.atom.asset_id
    try:
        score = score_service.score(asset_id)
    except Exception as exc:  # noqa: BLE001
        emit(
            "L3",
            "Resolver.rank",
            "score_signal_unavailable",
            f"step={step.id} asset={asset_id} err={exc}",
        )
        return selection

    base = selection.confidence
    multiplier = 1.0 + SCORE_SIGNAL_ALPHA * score.score
    final = min(1.0, max(0.0, base * multiplier))

    emit(
        "L3",
        "Resolver.rank",
        "score_signal_applied",
        (
            f"step={step.id} asset={asset_id} "
            f"base={base:.4f} score={score.score:.4f} "
            f"multiplier={multiplier:.4f} final={final:.4f} "
            f"history={'yes' if score.has_history else 'cold'}"
        ),
    )
    selection.confidence = final
    return selection


def _build_user_prompt(step: StepSpec, candidates: list[tuple[AtomDef, float]]) -> str:
    cand_block = []
    for i, (a, score) in enumerate(candidates):
        cand_block.append(
            f"### 候选 {i + 1}: {a.asset_id} (recall={score:.2f})\n"
            f"  subcategory: {a.subcategory}\n"
            f"  description: {a.description.strip()}\n"
            f"  tags: {a.tags}\n"
            f"  NOT_applicable: {a.NOT_applicable}\n"
        )
    return (
        f"## Step\n"
        f"  id: {step.id}\n"
        f"  verb: {step.verb}\n"
        f"  expected_output_kind: {step.expected_output_kind}\n"
        f"  constraints: {step.constraints}\n"
        f"  suggested_subcategory: {step.suggested_subcategory}\n"
        f"\n## 候选 (Top {len(candidates)})\n"
        + "\n".join(cand_block)
        + "\n## 你的输出"
    )


def _heuristic_llm_routing(verb: str) -> tuple[str, str]:
    """V1 heuristic; Phase 2 will use a learned classifier."""
    v = verb.lower()
    if any(k in v for k in ["抽取", "提取", "分类", "标签", "实体", "结构化"]):
        return "结构化抽取", "中"
    if any(k in v for k in ["分析", "写", "生成报告", "撰写", "周报", "日报"]):
        return "长文档分块总结", "中"
    if any(k in v for k in ["规划", "方案", "推理", "策略"]):
        return "长上下文 / 深度推理", "大"
    if any(k in v for k in ["改写", "润色", "标题"]):
        return "轻量判别", "小"
    return "通用对话", "中"
