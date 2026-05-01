"""LLM-driven retrieval-keyword extraction for newly generated assets (Batch G).

Closes the self-reinforcing loop's retrieval gap: when ``auto_register`` synthesizes
an :class:`AgentContract`, the existing path copies ``RequirementSpec.special_requirements``
into ``intent_keywords`` verbatim — but those tend to be implementation details
(specific API names, file paths, internal terms) rather than the **business
concepts** a future user will actually search for.

This module asks a light LLM for 5-10 business-concept keywords (mixed Chinese
and English, including synonyms and broader concepts), so that a related SOP
later can discover the generated agent through the standard ``RegistryHub.search``
path. On any failure (validation exhausted, network error, key missing) we fall
back to ``[product_name] + special_requirements`` so the system stays usable.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.core.llm.client import LLMClient
from app.core.stability.repair import generate_with_repair
from app.core.trace.bus import emit
from app.ontology import RequirementSpec


class IntentKeywordSet(BaseModel):
    """LLM output schema for retrieval keyword extraction."""

    keywords: list[str] = Field(..., min_length=3, max_length=12)

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {"keywords": ["周报生成", "团队报告", "weekly report", "progress report", "team summary"]},
                {"keywords": ["发票识别", "invoice OCR", "票据提取", "expense extraction"]},
            ]
        },
    )


SYSTEM_PROMPT = (
    "你是检索关键词工程师。给定一个产品的名称、原始 SOP、以及用户附加要求,"
    "你需要为这个产品提炼 5-10 个业务概念检索关键词,用于将来用户用相似 SOP 时能召回它。\n\n"
    "原则:\n"
    "1. 关键词应贴近业务场景/用户意图(如 '周报生成' / 'weekly report' / '团队报告' / '工时统计'),"
    "而不是实现细节(如具体 API 名、文件名、字段名、第三方库名)。\n"
    "2. 中英文关键词都要覆盖:中文 3-5 个 + 英文 2-4 个。\n"
    "3. 包含同义词和上位概念(如做'团队周报' → 关键词应含'周报', '团队报告', 'team report', 'progress report')。\n"
    "4. 关键词长度 1-12 字,避免长句和重复。\n"
    "5. 不要使用产品名本身,要给出能匹配同类需求的更通用词。\n\n"
    "只输出 JSON,严格匹配 schema。"
)


def _safe_seed_keywords(spec: RequirementSpec) -> list[str]:
    """Best-effort fallback when LLM extraction fails completely."""
    seed: list[str] = []
    if spec.product_name:
        seed.append(spec.product_name)
    seed.extend(s for s in (spec.special_requirements or []) if s and s.strip())
    if spec.product_type:
        seed.append(spec.product_type)
    # dedupe preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for kw in seed:
        low = kw.lower().strip()
        if low and low not in seen:
            seen.add(low)
            unique.append(kw.strip())
    return unique[:10]


async def extract_intent_keywords(
    llm: LLMClient,
    *,
    model: str,
    spec: RequirementSpec,
    source_sop: str | None = None,
) -> list[str]:
    """Derive 5-10 business-concept retrieval keywords for a freshly generated asset.

    On any failure, falls back to ``[product_name] + special_requirements``.
    """
    user_prompt = (
        f"产品名: {spec.product_name}\n"
        f"产品类型: {spec.product_type}\n"
        f"目标用户: {', '.join(spec.target_users) if spec.target_users else '(none)'}\n"
        f"核心页面/功能: {', '.join(spec.core_pages) if spec.core_pages else '(none)'}\n"
        f"特殊要求: {', '.join(spec.special_requirements) if spec.special_requirements else '(none)'}\n"
        f"原始 SOP: {source_sop or '(未记录)'}\n\n"
        "请输出 5-10 个业务检索关键词。"
    )
    try:
        result = await generate_with_repair(
            llm,
            model=model,
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            schema=IntentKeywordSet,
            max_repairs=2,
            max_tokens=500,
        )
        keywords = result.value.keywords
    except Exception as exc:  # noqa: BLE001
        emit(
            "L6", "Marketplace", "keywords_failed",
            f"product={spec.product_name} · {type(exc).__name__}: {exc}",
        )
        return _safe_seed_keywords(spec)

    seen: set[str] = set()
    unique: list[str] = []
    for kw in keywords:
        if not kw or not kw.strip():
            continue
        low = kw.lower().strip()
        if low not in seen:
            seen.add(low)
            unique.append(kw.strip())

    if not unique:
        unique = _safe_seed_keywords(spec)

    emit(
        "L6", "Marketplace", "keywords_extracted",
        f"product={spec.product_name} · keywords={len(unique)}",
        data={"keywords": unique},
    )
    return unique


__all__ = ["extract_intent_keywords", "IntentKeywordSet"]
