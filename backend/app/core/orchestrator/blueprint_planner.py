from __future__ import annotations

from app.core.llm.client import LLMClient
from app.core.stability.contracts import RequirementSpec, UIBlueprint
from app.core.stability.repair import RepairResult, generate_with_repair
from app.core.trace.bus import emit

BLUEPRINT_SYSTEM_PROMPT = """You translate a RequirementSpec (user intent) into a concrete UIBlueprint.

Rules:
- For each core_page in spec, produce exactly one PageBlueprint, preserving order
- route: "/" for the first page; others use a "/kebab-case" slug of the page title
- components: pick 1-3 items from ["hero","list","form","card-grid","table"] that best fit the page's purpose
  (lists/tables → ["table"] or ["list"]; detail/edit/create → ["form"]; landing/dashboard → ["hero","card-grid"])
- data_fields: infer 3-8 concrete field names consistent with the page and domain (snake_case)
- brand: infer color preferences from special_requirements; else default
  {"primary":"#6366f1","font":"Inter","radius":"md"}. primary MUST be "#RRGGBB" hex (6 hex digits).

Return JSON matching the provided schema exactly. No commentary, no markdown fence.
"""


class BlueprintPlanner:
    """Translate a RequirementSpec into a UIBlueprint via LLM + L4 Repair."""

    def __init__(self, llm: LLMClient, model: str) -> None:
        self._llm = llm
        self._model = model

    async def plan(self, spec: RequirementSpec) -> RepairResult:
        emit("L3", "BlueprintPlanner", "start",
             f"spec → blueprint · pages={len(spec.core_pages)} · product={spec.product_name}")
        user_prompt = (
            "RequirementSpec JSON:\n"
            f"{spec.model_dump_json(indent=2)}\n\n"
            "Produce a UIBlueprint JSON matching the schema."
        )
        result = await generate_with_repair(
            self._llm,
            model=self._model,
            system_prompt=BLUEPRINT_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            schema=UIBlueprint,
            max_repairs=3,
            max_tokens=4000,
        )
        emit("L3", "BlueprintPlanner", "done",
             f"✓ pages={len(result.value.pages)} · attempts={result.attempts} · fallback={result.used_fallback}")
        return result
