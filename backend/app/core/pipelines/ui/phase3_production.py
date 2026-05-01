from __future__ import annotations

import json

from app.core.llm.client import LLMClient
from app.core.pipelines.ui.phase2_prototype import PrototypeVariant
from app.core.pipelines.ui.prompts import PHASE3_PRODUCTION_CODE_SYSTEM
from app.core.stability.contracts import CodeArtifact, UIBlueprint
from app.core.stability.repair import RepairResult, generate_with_repair


async def generate_production_code(
    llm: LLMClient,
    *,
    model: str,
    blueprint: UIBlueprint,
    chosen_variant: PrototypeVariant,
    max_repairs: int = 3,
) -> RepairResult:
    """Generate Vite + React + shadcn/ui CodeArtifact. Returns RepairResult[CodeArtifact]."""
    schema_json = json.dumps(CodeArtifact.model_json_schema(), ensure_ascii=False, indent=2)
    user_prompt = "\n\n".join(
        [
            "Convert the selected HTML prototype into a production-ready code artifact.",
            f"Blueprint JSON:\n{blueprint.model_dump_json(indent=2)}",
            f"Chosen variant JSON:\n{chosen_variant.model_dump_json(indent=2)}",
            "Apply the Phase 3 workflow constraints from the design workflow reference.",
            f"Target schema:\n{schema_json}",
        ]
    )
    return await generate_with_repair(
        llm,
        model=model,
        system_prompt=PHASE3_PRODUCTION_CODE_SYSTEM,
        user_prompt=user_prompt,
        schema=CodeArtifact,
        max_repairs=max_repairs,
        max_tokens=48000,
    )
