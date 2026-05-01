from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field

from app.core.llm.client import LLMClient
from app.core.pipelines.ui.prompts import PHASE2_HTML_PROTOTYPE_SYSTEM
from app.core.stability.contracts import UIBlueprint
from app.core.stability.repair import RepairResult, generate_with_repair


class PrototypeVariant(BaseModel):
    """A visual prototype candidate for the same blueprint."""

    name: str = Field(..., description="变体名,如 'compact-sidebar'")
    description: str = Field(..., description="一句话风格描述")
    html: str = Field(..., description="完整 HTML 文档,React+Babel 可直接打开")

    model_config = ConfigDict(extra="forbid")


class Phase2Output(BaseModel):
    """Three HTML prototype variants for a blueprint."""

    variants: list[PrototypeVariant] = Field(..., min_length=3, max_length=3)

    model_config = ConfigDict(extra="forbid")


async def generate_prototypes(
    llm: LLMClient, *, model: str, blueprint: UIBlueprint, max_repairs: int = 3
) -> RepairResult:
    """Generate 3 HTML prototype variants. Returns RepairResult[Phase2Output]."""
    schema_json = json.dumps(Phase2Output.model_json_schema(), ensure_ascii=False, indent=2)
    user_prompt = "\n\n".join(
        [
            "Generate three HTML prototype variants for this blueprint.",
            f"Blueprint JSON:\n{blueprint.model_dump_json(indent=2)}",
            "Apply the Phase 2 workflow constraints from the design workflow reference.",
            f"Target schema:\n{schema_json}",
        ]
    )
    return await generate_with_repair(
        llm,
        model=model,
        system_prompt=PHASE2_HTML_PROTOTYPE_SYSTEM,
        user_prompt=user_prompt,
        schema=Phase2Output,
        max_repairs=max_repairs,
        max_tokens=32000,
    )
