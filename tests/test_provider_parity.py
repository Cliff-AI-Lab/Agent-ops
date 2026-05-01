from __future__ import annotations

import json
import re
from typing import Any

import pytest

from app.core.llm.client import ChatMessage
from app.core.orchestrator.meta_agent import MetaAgent
from app.core.stability.contracts import BrandTokens, PageBlueprint, RequirementSpec, UIBlueprint

SOP_SAMPLES = [
    "做一个公司内部的订单管理 SaaS,有仪表盘和订单列表。",
    "我要一个博客平台:首页 + 文章列表 + 详情页。",
    "HR 系统,员工列表 + 入职表单 + 请假审批。",
]

CANNED_RESPONSES: dict[str, dict[int, list[str]]] = {
    "light": {
        0: [
            json.dumps(
                {
                    "product_name": "Order Hub",
                    "target_users": ["operations", "sales ops"],
                    "core_pages": ["dashboard", "order list"],
                    "reference_brands": ["Linear"],
                    "special_requirements": ["internal tooling", "cn copy"],
                },
                ensure_ascii=False,
            )
        ],
        1: [
            json.dumps(
                {
                    "product_name": "Blog Forge",
                    "target_users": ["editors", "readers"],
                    "core_pages": ["home", "article list", "article detail"],
                    "reference_brands": ["Notion"],
                    "special_requirements": ["clean typography"],
                },
                ensure_ascii=False,
            )
        ],
        2: [
            json.dumps(
                {
                    "product_name": "HR Flow",
                    "target_users": ["hr", "employees"],
                    "core_pages": ["employee list", "onboarding form", "leave approval"],
                    "reference_brands": ["Workday"],
                    "special_requirements": ["audit trail"],
                },
                ensure_ascii=False,
            )
        ],
    },
    "mid": {
        0: [
            json.dumps(
                {
                    "product_name": "Order Hub Pro",
                    "target_users": ["ops", "sales operations"],
                    "core_pages": ["dashboard", "order list"],
                    "reference_brands": ["Linear", "Stripe"],
                    "special_requirements": ["internal tooling"],
                },
                ensure_ascii=False,
            )
        ],
        1: [
            json.dumps(
                {
                    "product_name": "Blog Forge Studio",
                    "target_users": ["content editors", "readers"],
                    "core_pages": ["home", "article list", "article detail"],
                    "reference_brands": ["Notion", "Substack"],
                    "special_requirements": ["clean typography", "editor friendly"],
                },
                ensure_ascii=False,
            )
        ],
        2: [
            json.dumps(
                {
                    "product_name": "HR Flow Ops",
                    "target_users": ["hr admins", "employees"],
                    "core_pages": ["employee list", "onboarding form", "leave approval"],
                    "reference_brands": ["Workday", "BambooHR"],
                    "special_requirements": ["audit trail", "approval status"],
                },
                ensure_ascii=False,
            )
        ],
    },
    "heavy": {
        0: [
            json.dumps(
                {
                    "product_name": "Order Hub Suite",
                    "target_users": ["operations managers", "sales ops"],
                    "core_pages": ["dashboard", "order list"],
                    "reference_brands": ["Linear"],
                    "special_requirements": ["cn localization"],
                },
                ensure_ascii=False,
            )
        ],
        1: [
            json.dumps(
                {
                    "product_name": "Blog Forge Plus",
                    "target_users": ["editorial team", "readers"],
                    "core_pages": ["home", "article list", "article detail"],
                    "reference_brands": ["Notion"],
                    "special_requirements": ["editorial workflow"],
                },
                ensure_ascii=False,
            )
        ],
        2: [
            json.dumps(
                {
                    "product_name": "HR Flow Suite",
                    "target_users": ["hr operations", "employees"],
                    "core_pages": ["employee list", "onboarding form", "leave approval"],
                    "reference_brands": ["Workday"],
                    "special_requirements": ["approval workflow"],
                },
                ensure_ascii=False,
            )
        ],
    },
}


class FakeLLM:
    """Deterministic mock LLM for parity testing. Returns canned responses."""

    def __init__(self, model_responses: dict[str, list[str]]) -> None:
        self._model_responses = {model: list(responses) for model, responses in model_responses.items()}
        self.calls: list[dict[str, Any]] = []

    async def chat(
        self,
        model: str,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> ChatMessage:
        self.calls.append(
            {
                "model": model,
                "messages": [message.model_copy(deep=True) for message in messages],
                "tools": tools,
                "temperature": temperature,
            }
        )
        response = self._model_responses[model].pop(0)
        return ChatMessage(role="assistant", content=response)


@pytest.fixture(autouse=True)
def patch_retry_timers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable retry sleeps for deterministic tests."""

    async def _no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("app.core.llm.retry.asyncio.sleep", _no_sleep)
    monkeypatch.setattr("app.core.llm.retry.random.uniform", lambda _a, _b: 1.0)


def _compute_drift_score(spec_a: RequirementSpec, spec_b: RequirementSpec) -> float:
    """Return 0-1, 0=identical, 1=totally different."""
    blueprint_a = _spec_to_blueprint(spec_a)
    blueprint_b = _spec_to_blueprint(spec_b)
    page_count_score = abs(len(blueprint_a.pages) - len(blueprint_b.pages)) / max(
        len(blueprint_a.pages), len(blueprint_b.pages), 1
    )
    component_score = _jaccard_distance(
        {component for page in blueprint_a.pages for component in page.components},
        {component for page in blueprint_b.pages for component in page.components},
    )
    field_score = _jaccard_distance(
        {field for page in blueprint_a.pages for field in page.data_fields},
        {field for page in blueprint_b.pages for field in page.data_fields},
    )
    return round((page_count_score * 0.4) + (component_score * 0.3) + (field_score * 0.3), 4)


def _spec_to_blueprint(spec: RequirementSpec) -> UIBlueprint:
    pages: list[PageBlueprint] = []
    for index, page_name in enumerate(spec.core_pages):
        slug = _slugify(page_name, fallback=f"page-{index + 1}")
        pages.append(
            PageBlueprint(
                route="/" if index == 0 else f"/{slug}",
                title=page_name.title(),
                components=_components_for_page(page_name, is_first=index == 0),
                data_fields=_fields_for_page(page_name),
            )
        )
    return UIBlueprint(
        pages=pages,
        brand=BrandTokens(primary="#0F62FE", font="IBM Plex Sans", radius="md"),
    )


def _components_for_page(page_name: str, *, is_first: bool) -> list[str]:
    lowered = page_name.lower()
    components: list[str] = []
    if is_first:
        components.append("hero")
    if any(keyword in lowered for keyword in ("dashboard", "home", "overview")):
        components.extend(["card-grid", "table"])
    elif any(keyword in lowered for keyword in ("list", "order", "employee", "article")):
        components.extend(["table", "list"])
    elif any(keyword in lowered for keyword in ("form", "approval", "request", "detail")):
        components.extend(["form", "card-grid"])
    else:
        components.extend(["list", "card-grid"])
    return list(dict.fromkeys(components))[:3]


def _fields_for_page(page_name: str) -> list[str]:
    tokens = [_slugify(token, fallback="field") for token in re.findall(r"[a-zA-Z]+", page_name.lower())]
    fields = {token.replace("-", "_") for token in tokens if token}
    if "dashboard" in page_name.lower() or "home" in page_name.lower():
        fields.update({"headline", "summary", "metrics"})
    if "list" in page_name.lower() or "order" in page_name.lower() or "employee" in page_name.lower():
        fields.update({"id", "status", "owner"})
    if "form" in page_name.lower() or "approval" in page_name.lower():
        fields.update({"name", "status", "submitted_at"})
    return sorted(fields or {"title", "summary"})


def _jaccard_distance(left: set[str], right: set[str]) -> float:
    union = left | right
    if not union:
        return 0.0
    return 1.0 - (len(left & right) / len(union))


def _slugify(value: str, *, fallback: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or fallback


@pytest.mark.parametrize("sop_index", range(len(SOP_SAMPLES)))
@pytest.mark.asyncio
async def test_model_drift_is_bounded(sop_index: int) -> None:
    """For each SOP, MetaAgent output across 3 mock models must differ <=15%."""
    llm = FakeLLM({model_id: responses[sop_index] for model_id, responses in CANNED_RESPONSES.items()})
    specs: dict[str, RequirementSpec] = {}

    for model_id in CANNED_RESPONSES:
        agent = MetaAgent(llm, light_model="light", heavy_model=model_id)
        specs[model_id] = await agent.build_spec(SOP_SAMPLES[sop_index], [])

    scores = [
        _compute_drift_score(specs["light"], specs["mid"]),
        _compute_drift_score(specs["light"], specs["heavy"]),
        _compute_drift_score(specs["mid"], specs["heavy"]),
    ]

    assert len(llm.calls) == 3
    assert max(scores) <= 0.15
