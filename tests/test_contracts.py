from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.stability.contracts import (
    BrandTokens,
    CodeArtifact,
    FileEntry,
    PageBlueprint,
    RequirementSpec,
    UIBlueprint,
)


def test_contract_examples_are_in_json_schema() -> None:
    for model in (RequirementSpec, PageBlueprint, BrandTokens, UIBlueprint, FileEntry, CodeArtifact):
        schema = model.model_json_schema()
        assert "examples" in schema
        assert len(schema["examples"]) >= 3


def test_invalid_contract_payload_raises_validation_error() -> None:
    with pytest.raises(ValidationError):
        RequirementSpec.model_validate(
            {
                "product_name": "Broken",
                "target_users": "not-a-list",
                "core_pages": [],
            }
        )

    with pytest.raises(ValidationError):
        BrandTokens.model_validate({"primary": "blue", "font": "Sans", "radius": "xl"})


def test_model_json_schema_contains_required_and_properties() -> None:
    for model in (RequirementSpec, UIBlueprint, CodeArtifact):
        schema = model.model_json_schema()
        assert isinstance(schema, dict)
        assert "required" in schema
        assert "properties" in schema


def test_valid_payload_round_trips_into_python_objects() -> None:
    requirement = RequirementSpec.model_validate(
        {
            "product_name": "Ops Board",
            "target_users": ["operators"],
            "core_pages": ["dashboard"],
            "reference_brands": ["Notion"],
            "special_requirements": ["large text"],
        }
    )
    ui_blueprint = UIBlueprint.model_validate(
        {
            "pages": [
                {
                    "route": "/",
                    "title": "Dashboard",
                    "components": ["hero", "table"],
                    "data_fields": ["headline", "alerts"],
                }
            ],
            "brand": {"primary": "#123ABC", "font": "IBM Plex Sans", "radius": "md"},
        }
    )
    code_artifact = CodeArtifact.model_validate(
        {
            "files": [{"path": "app/page.tsx", "content": "export default function Page() { return null; }"}],
            "dependencies": {"next": "^14.2.0"},
            "entrypoint": "app/page.tsx",
        }
    )

    assert isinstance(requirement, RequirementSpec)
    assert isinstance(ui_blueprint.pages[0], PageBlueprint)
    assert isinstance(ui_blueprint.brand, BrandTokens)
    assert isinstance(code_artifact.files[0], FileEntry)
