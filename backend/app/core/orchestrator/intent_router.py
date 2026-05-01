"""IntentRouter — LLM reads the Wiki shelf and emits an authoritative WorkflowSpec.

Pipeline (blueprint §13 Planner, user-vision "意图识别 → Wiki 索引 → 拼装"):

    SOP
     │
     ▼
  [light LLM] extract_intent → IntentBreakdown(intent_summary, sub_tasks)
     │
     ▼
  [RegistryHub] search candidates across atom / composite / agent tiers
     │
     ▼
  [heavy LLM] plan_workflow → WorkflowSpec (JSON, typed, referencing only
     registered ids, never executing)
     │
     ▼
  [validate_spec] every step.capability / tools_used ref exists in registry
     │
     ├── ok     → IntentRouteResult
     └── fail   → L4 Repair (feed validation errors back to LLM up to max_repairs)

The IntentRouter does NOT execute. Its output is the **audit boundary** —
WorkflowSpec YAML/JSON that a human or policy layer can review before the
Workflow Engine runs it (blueprint §3.1 deterministic shell).
"""
from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field

from app.core.llm.client import LLMClient
from app.core.stability.repair import RepairResult, generate_with_repair
from app.core.trace.bus import emit
from app.ontology import WorkflowSpec
from app.registry.registry_hub import RegistryEntry
from app.registry.registry_hub import search as hub_search

# ---- Intermediate LLM outputs ----


class IntentSubTask(BaseModel):
    """One atomic sub-task parsed from the SOP."""

    task: str = Field(..., description="What needs to be done (one sentence).")
    keywords: list[str] = Field(
        default_factory=list,
        description="Retrieval hints — words likely to appear in capability names / intent_keywords.",
    )

    model_config = ConfigDict(extra="forbid")


class IntentBreakdown(BaseModel):
    """LLM output of intent-extraction stage."""

    intent_summary: str = Field(..., min_length=1)
    sub_tasks: list[IntentSubTask] = Field(..., min_length=1, max_length=10)

    model_config = ConfigDict(extra="forbid")


# ---- Final result ----


class IntentRouteResult(BaseModel):
    """Everything IntentRouter produces for one SOP."""

    user_sop: str
    intent_summary: str
    sub_tasks: list[IntentSubTask]
    candidate_shortlist: list[RegistryEntry] = Field(default_factory=list)
    workflow_spec: WorkflowSpec
    spec_valid: bool
    validation_errors: list[str] = Field(default_factory=list)
    fallback_used: bool = False
    planner_attempts: int = 1

    model_config = ConfigDict(extra="forbid")


INTENT_EXTRACT_SYSTEM = (
    "你是需求分解专家。用户给你一段自然语言 SOP,你需要:\n"
    "1. 用一句话总结用户的整体意图(intent_summary,≤ 40 字)\n"
    "2. 把 SOP 拆成 1-8 个原子子任务(sub_tasks),每个描述'需要做什么'\n"
    "3. 对每个子任务,给出 3-8 个关键动作词 / 检索关键字(用于匹配注册中心里的能力)\n"
    "\n"
    "只输出 JSON,结构严格匹配 schema,不要多余文字。\n"
    "关键词中英文皆可,贴近候选能力的 intent_keywords 字段(如 '识别身份证' / 'OCR 发票' / 'web research')。"
)


PLANNER_SYSTEM = (
    "你是 WorkflowSpec 规划器。\n"
    "给定用户 SOP + 已分解子任务 + 候选能力清单(每条含 id / kind / 描述 / intent_keywords),\n"
    "生成一份**显式、可审计、可重放**的 WorkflowSpec JSON。\n"
    "\n"
    "严格规则:\n"
    "1. step.capability 只能填 candidates 中存在的 id,**不准编造新能力**\n"
    "2. 每个 step 有唯一 id(建议 step-1, step-2, ...),depends_on 写前置 step id\n"
    "3. input_mapping 用 `inputs.<field>`(来自用户输入)或 `steps.<step_id>.<output_field>`(来自上游)\n"
    "4. 风险操作(删除、对外发送)设 approval_required=true\n"
    "5. 若某子任务在 candidates 中找不到合适匹配,**不要**硬塞无关能力 — 宁缺毋滥\n"
    "6. 单个 workflow 的 steps 应在 1-8 个之间,避免过长\n"
    "\n"
    "只输出 WorkflowSpec JSON(必须能通过 schema 校验),不要多余文字。"
)


def _format_candidate(entry: RegistryEntry) -> str:
    """Compact one-block representation per candidate for the planner prompt."""
    lines = [
        f"### [{entry.kind}] {entry.id}  (v{entry.version})",
        f"- name: {entry.name}",
        f"- description: {entry.description.strip().splitlines()[0][:180] if entry.description else ''}",
    ]
    if entry.intent_keywords:
        lines.append(f"- intent_keywords: {', '.join(entry.intent_keywords)}")
    raw = entry.raw or {}
    input_schema = raw.get("input_schema")
    output_schema = raw.get("output_schema")
    if isinstance(input_schema, dict) and input_schema:
        lines.append(
            f"- input keys: {', '.join(list((input_schema.get('properties') or {}).keys())[:6])}"
        )
    if isinstance(output_schema, dict) and output_schema:
        lines.append(
            f"- output keys: {', '.join(list((output_schema.get('properties') or {}).keys())[:6])}"
        )
    return "\n".join(lines)


class IntentRouter:
    """SOP → WorkflowSpec planner, backed by RegistryHub search and L4 Repair."""

    def __init__(
        self,
        llm: LLMClient,
        light_model: str,
        heavy_model: str,
        *,
        shortlist_top_k: int = 8,
        min_shortlist: int = 5,
        max_repairs: int = 3,
    ) -> None:
        self._llm = llm
        self._light_model = light_model
        self._heavy_model = heavy_model
        self._shortlist_top_k = shortlist_top_k
        self._min_shortlist = min_shortlist
        self._max_repairs = max_repairs

    # ---- Public API ----
    async def plan(self, user_sop: str) -> IntentRouteResult:
        """Drive the full intent → shortlist → plan → validate pipeline."""
        emit("L3", "IntentRouter", "start", f"sop_len={len(user_sop)}")

        breakdown = await self._extract_intent(user_sop)
        emit(
            "L3", "IntentRouter", "intent_extracted",
            f"intent={breakdown.intent_summary[:60]} · sub_tasks={len(breakdown.sub_tasks)}",
            data={"sub_task_count": len(breakdown.sub_tasks)},
        )

        shortlist = self._gather_candidates(breakdown.sub_tasks)
        emit(
            "L3", "IntentRouter", "candidates_found",
            f"shortlist={len(shortlist)}",
            data={"shortlist_ids": [c.id for c in shortlist]},
        )

        plan_result = await self._plan_workflow(user_sop, breakdown, shortlist)
        spec = plan_result.value
        errs = _validate_spec_refs(spec, shortlist)
        spec_valid = not errs
        emit(
            "L3", "IntentRouter", "spec_validated" if spec_valid else "spec_invalid",
            f"steps={len(spec.steps)} · errors={len(errs)} · attempts={plan_result.attempts} · fallback={plan_result.used_fallback}",
            data={"errors": errs},
        )

        return IntentRouteResult(
            user_sop=user_sop,
            intent_summary=breakdown.intent_summary,
            sub_tasks=list(breakdown.sub_tasks),
            candidate_shortlist=shortlist,
            workflow_spec=spec,
            spec_valid=spec_valid,
            validation_errors=errs,
            fallback_used=plan_result.used_fallback,
            planner_attempts=plan_result.attempts,
        )

    # ---- Internal steps ----
    async def _extract_intent(self, user_sop: str) -> IntentBreakdown:
        user_prompt = (
            f"用户 SOP:\n{user_sop}\n\n"
            f"输出 JSON schema:\n{json.dumps(IntentBreakdown.model_json_schema(), ensure_ascii=False, indent=2)}"
        )
        result = await generate_with_repair(
            self._llm,
            model=self._light_model,
            system_prompt=INTENT_EXTRACT_SYSTEM,
            user_prompt=user_prompt,
            schema=IntentBreakdown,
            max_repairs=self._max_repairs,
            max_tokens=2000,
        )
        return result.value

    def _gather_candidates(self, sub_tasks: list[IntentSubTask]) -> list[RegistryEntry]:
        """For each sub-task, pull top-K from RegistryHub and union (dedupe by id).

        Batch G — also ensures cross-tier coverage so generated agents are not
        starved by atom/composite hits: every active agent that scores any
        keyword overlap (case-insensitive substring match against id / name /
        description / intent_keywords / tags) is added to the shortlist, and
        the final shortlist size is padded up to ``min_shortlist``.
        """
        from app.registry.registry_hub import list_all

        seen: dict[str, RegistryEntry] = {}

        def _ingest(hits: list[RegistryEntry]) -> None:
            for h in hits:
                key = f"{h.kind}:{h.id}"
                if key not in seen:
                    seen[key] = h

        for sub in sub_tasks:
            task_query = sub.task
            _ingest(hub_search(task_query)[: self._shortlist_top_k])
            for kw in sub.keywords:
                _ingest(hub_search(kw)[: self._shortlist_top_k])

        # Cross-tier boost: every agent whose own intent_keywords overlap any
        # of the sub-task tokens earns a place. This is the gate that lets
        # generated agents (whose keywords were extracted at auto_register)
        # appear in the shortlist for related SOPs.
        all_tokens: set[str] = set()
        for sub in sub_tasks:
            for chunk in (sub.task, *sub.keywords):
                for tok in str(chunk).lower().split():
                    if len(tok) >= 2:
                        all_tokens.add(tok.strip(",.;:!?'\"()[]{}"))
        if all_tokens:
            for entry in list_all(activation_status="active"):
                key = f"{entry.kind}:{entry.id}"
                if key in seen:
                    continue
                haystack = " ".join([
                    entry.id, entry.name, entry.description,
                    " ".join(entry.tags),
                    " ".join(entry.intent_keywords),
                ]).lower()
                if any(tok and tok in haystack for tok in all_tokens):
                    seen[key] = entry

        # Pad up to ``min_shortlist`` with diverse active entries (one per kind)
        # so the planner always has at least a basic palette to work with.
        if len(seen) < self._min_shortlist:
            extras = list_all(activation_status="active")
            kinds_seen = {e.kind for e in seen.values()}
            # First pass: cover any missing kinds (atom / composite / agent)
            for entry in extras:
                if len(seen) >= self._min_shortlist:
                    break
                if entry.kind in kinds_seen:
                    continue
                key = f"{entry.kind}:{entry.id}"
                if key not in seen:
                    seen[key] = entry
                    kinds_seen.add(entry.kind)
            # Second pass: top up by raw alphabetical order
            for entry in extras:
                if len(seen) >= self._min_shortlist:
                    break
                key = f"{entry.kind}:{entry.id}"
                if key not in seen:
                    seen[key] = entry

        return list(seen.values())

    async def _plan_workflow(
        self,
        user_sop: str,
        breakdown: IntentBreakdown,
        shortlist: list[RegistryEntry],
    ) -> RepairResult:
        candidates_block = "\n\n".join(_format_candidate(e) for e in shortlist) or "(no candidates)"
        sub_task_block = "\n".join(
            f"- {st.task}  [keywords: {', '.join(st.keywords)}]" for st in breakdown.sub_tasks
        )
        user_prompt = (
            f"用户 SOP:\n{user_sop}\n\n"
            f"意图摘要: {breakdown.intent_summary}\n\n"
            f"子任务分解:\n{sub_task_block}\n\n"
            f"候选能力清单:\n{candidates_block}\n\n"
            f"请输出 WorkflowSpec JSON。每个 step.capability 必须来自候选清单的 id(字段 id)。"
        )
        return await generate_with_repair(
            self._llm,
            model=self._heavy_model,
            system_prompt=PLANNER_SYSTEM,
            user_prompt=user_prompt,
            schema=WorkflowSpec,
            max_repairs=self._max_repairs,
            max_tokens=8000,
        )


def _validate_spec_refs(spec: WorkflowSpec, shortlist: list[RegistryEntry]) -> list[str]:
    """Check every step.capability against shortlist + full registry.

    Returns a list of human-readable error messages (empty = clean).
    """
    errors: list[str] = []
    shortlist_ids = {e.id for e in shortlist}

    # Import lazily to avoid at-module-load cost in test contexts
    from app.registry.registry_hub import list_all

    registry_ids = {e.id for e in list_all()}

    step_ids: set[str] = set()
    for step in spec.steps:
        if step.id in step_ids:
            errors.append(f"duplicate step id: {step.id}")
        step_ids.add(step.id)
        if step.capability not in shortlist_ids and step.capability not in registry_ids:
            errors.append(
                f"step '{step.id}' references unknown capability '{step.capability}' "
                "(not in shortlist and not in full registry)"
            )
        for dep in step.depends_on:
            # Depends-on resolution is validated in a second pass
            pass

    # Second pass for depends_on references
    for step in spec.steps:
        for dep in step.depends_on:
            if dep not in step_ids:
                errors.append(f"step '{step.id}' depends_on unknown step '{dep}'")

    return errors


__all__ = [
    "IntentRouter",
    "IntentRouteResult",
    "IntentBreakdown",
    "IntentSubTask",
]
